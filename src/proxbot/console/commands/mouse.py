"""Tier 2 pointer input: click, mouse-move, mouse-test."""

import argparse
import base64
import contextlib
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import socket
import ssl
import struct
import sys
import time
import urllib.parse
from typing import Any

from PIL import Image

from ...common.config import Config
from ...common.constants import (
    EXIT_API, EXIT_AUTH, EXIT_CONFIG, EXIT_INTERNAL, EXIT_NOOP, EXIT_SAFETY,
    EXIT_SUCCESS, EXIT_TIMEOUT, EXIT_VALIDATION, REDACTED, REPO_ROOT,
)
from ...common.logging_ import Logger
from ...common.client import ProxmoxClient
from ...common.redact import redact_string
from ...common.render import emit_error, emit_output, render_output
from ...common.safety import require_destructive, require_execute
from ..broker import (
    ConsoleConfig, _capture_after_input, _capture_serial_text,
    _capture_vnc_screenshot, _check_eligibility, _check_emergency_stop,
    _console_error_result, _effective_delay, _find_recent_snapshot,
    _log_console_event, _new_session_id, cli,
    _parse_api_url_parts, _require_node, _save_session_event, _session_files,
    _vnc_session, _wait_for_task, _write_screenshot,
    DEFAULT_CONSOLE_KEY_DELAY, DEFAULT_CONSOLE_SESSION_LOG_DIR,
    DEFAULT_CONSOLE_WATCH_INTERVAL, CLI_PATH,
)
from ..keys import (
    BASE_KEY_ALLOWLIST, COMMON_COMBO_ALLOWLIST, KEY_TOKEN_ALIASES,
    RUN_COMMAND_ALLOWLIST, SEND_KEY_ALLOWLIST, SHELL_METACHARACTERS_RE,
    TYPE_ALLOWED_CONTROLS, US_SHIFTED_TYPE_CHARS, VNC_BUTTON_LEFT,
    VNC_BUTTON_MIDDLE, VNC_BUTTON_RIGHT, VNC_BUTTON_SCROLL_DOWN,
    VNC_BUTTON_SCROLL_UP, X11_KEYSYM, INSTALL_PRESETS,
    _canonical_key_token, _canonical_single_key_token, _combo_to_events,
    _is_valid_key_token, _key_name_to_events, _key_sequence_summary,
    _looks_like_modifier, _parse_key_sequence, _send_key_events,
    _split_key_combo, _strip_key_wrappers, _text_to_events,
    _type_text_has_shell_metacharacters, _validate_macro_action,
    _MODIFIER_ALLOWLIST, _MODIFIER_KEYS,
)
from .read import _ensure_vm_running
from ..protocol import (
    _SerialClient, _VNCClient, _WebSocketClient, _WebSocketFrame,
    _generate_websocket_key, _websocket_accept_key, _vnc_auth_response,
    _vnc_des_key,
)

def _input_vnc_session(
    node: str,
    config: ConsoleConfig,
    client: ProxmoxClient,
    logger: Logger,
    vmid: int,
    args: argparse.Namespace,
) -> tuple[str, str, str, pathlib.Path] | None:
    """Open a VNC proxy session and return (ticket, password, port, screenshot_path)."""
    vnc_endpoint = f"/nodes/{node}/qemu/{vmid}/vncproxy"
    vnc_resp = client.post(vnc_endpoint, {"websocket": 1})
    logger.log(
        event="console_vncproxy",
        argv=sys.argv[1:],
        method="POST",
        endpoint=vnc_endpoint,
        status_code=vnc_resp.get("status_code"),
        duration_ms=vnc_resp.get("duration_ms"),
        ok=vnc_resp["ok"],
        response_summary=f"node={node} vmid={vmid}",
        error=vnc_resp.get("error"),
    )
    if not vnc_resp["ok"]:
        result = {"ok": False, "data": vnc_resp.get("data"), "error": vnc_resp.get("error")}
        emit_output(result, args.format, title=f"Console Input VM {vmid}")
        return None

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")
    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Input VM {vmid}")
        return None

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Input VM {vmid}")
        return None

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"
    return ticket, password, port, screenshot_path


def cmd_click(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 3)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Click",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    guard = require_destructive(args, str(vmid))
    if guard is not None:
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    stop = _check_emergency_stop(config)
    if stop is not None:
        emit_output(stop, args.format, title="Console Click")
        return EXIT_SAFETY

    x = getattr(args, "x", 0)
    y = getattr(args, "y", 0)
    button = getattr(args, "button", "left")
    clicks = getattr(args, "clicks", 1)

    running_check = _ensure_vm_running(args, config, client, logger, str(vmid))
    if running_check is not None and not running_check.get("ok"):
        result = _console_error_result(
            running_check.get("error_type", "console_error"),
            running_check.get("error", f"VM {vmid} is not running"),
        )
        emit_output(result, args.format, title=f"Console Click VM {vmid}")
        return EXIT_API

    session = _input_vnc_session(node, config, client, logger, vmid, args)
    if session is None:
        return EXIT_API
    ticket, password, port, screenshot_path = session

    session_id = _new_session_id()
    ok = False
    frame_width: int | None = None
    frame_height: int | None = None
    clamped_x: int | None = None
    clamped_y: int | None = None
    screenshot_mode: str | None = None
    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            frame_width = vnc.width
            frame_height = vnc.height
            clamped_x, clamped_y = vnc.clamp_pointer_coordinates(x, y)
            if not vnc.mouse_click(x, y, button=button, clicks=clicks, hold_seconds=args.hold_ms / 1000):
                raise ConnectionError("Failed to send VNC mouse click")
            time.sleep(_effective_delay(args, config))
            screenshot_mode = _capture_after_input(vnc, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        logger.log(
            event="console_click",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} x={x} y={y} button={button} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_click",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 3,
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "clamped_x": clamped_x,
        "clamped_y": clamped_y,
        "screenshot_mode": screenshot_mode,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_click",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "clamped_x": clamped_x,
            "clamped_y": clamped_y,
            "screenshot_mode": screenshot_mode,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to click on VM console")
        emit_output(result, args.format, title=f"Console Click VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
        "session_id": session_id,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "clamped_x": clamped_x,
        "clamped_y": clamped_y,
        "screenshot_mode": screenshot_mode,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Click VM {vmid}")
    return EXIT_SUCCESS


def cmd_mouse_move(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 2)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Mouse-Move",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    guard = require_execute(args, f"mouse-move to VM {vmid}")
    if guard is not None:
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    running_check = _ensure_vm_running(args, config, client, logger, str(vmid))
    if running_check is not None and not running_check.get("ok"):
        result = _console_error_result(
            running_check.get("error_type", "console_error"),
            running_check.get("error", f"VM {vmid} is not running"),
        )
        emit_output(result, args.format, title=f"Console Mouse-Move VM {vmid}")
        return EXIT_API

    x = getattr(args, "x", 0)
    y = getattr(args, "y", 0)

    session = _input_vnc_session(node, config, client, logger, vmid, args)
    if session is None:
        return EXIT_API
    ticket, password, port, screenshot_path = session

    session_id = _new_session_id()
    ok = False
    frame_width: int | None = None
    frame_height: int | None = None
    clamped_x: int | None = None
    clamped_y: int | None = None
    screenshot_mode: str | None = None
    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            frame_width = vnc.width
            frame_height = vnc.height
            clamped_x, clamped_y = vnc.clamp_pointer_coordinates(x, y)
            if not vnc.mouse_move(x, y):
                raise ConnectionError("Failed to send VNC mouse move")
            time.sleep(_effective_delay(args, config))
            screenshot_mode = _capture_after_input(vnc, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        ok = False
        logger.log(
            event="console_mouse_move",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} x={x} y={y} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_mouse_move",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 2,
        "x": x,
        "y": y,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "clamped_x": clamped_x,
        "clamped_y": clamped_y,
        "screenshot_mode": screenshot_mode,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_mouse_move",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "x": x,
            "y": y,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "clamped_x": clamped_x,
            "clamped_y": clamped_y,
            "screenshot_mode": screenshot_mode,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to move mouse on VM console")
        emit_output(result, args.format, title=f"Console Mouse-Move VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "x": x,
        "y": y,
        "session_id": session_id,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "clamped_x": clamped_x,
        "clamped_y": clamped_y,
        "screenshot_mode": screenshot_mode,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Mouse-Move VM {vmid}")
    return EXIT_SUCCESS


def _parse_mouse_points(value: str | None) -> list[tuple[int, int]] | None:
    if not value:
        return None
    points: list[tuple[int, int]] = []
    for token in value.split(";"):
        token = token.strip()
        if not token:
            continue
        x_str, sep, y_str = token.partition(",")
        if sep != ",":
            return None
        try:
            points.append((int(x_str.strip()), int(y_str.strip())))
        except ValueError:
            return None
    return points or None


def cmd_mouse_test(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 2)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Mouse-Test",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    guard = require_execute(args, f"mouse-test on VM {vmid}")
    if guard is not None:
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    points = _parse_mouse_points(getattr(args, "points", None))
    if points is None and getattr(args, "points", None):
        result = _console_error_result("validation", "--points must use 'x,y;x,y' syntax")
        emit_output(result, args.format, title="Console Mouse-Test")
        return EXIT_VALIDATION

    running_check = _ensure_vm_running(args, config, client, logger, str(vmid))
    if running_check is not None and not running_check.get("ok"):
        result = _console_error_result(
            running_check.get("error_type", "console_error"),
            running_check.get("error", f"VM {vmid} is not running"),
        )
        emit_output(result, args.format, title=f"Console Mouse-Test VM {vmid}")
        return EXIT_API

    session = _input_vnc_session(node, config, client, logger, vmid, args)
    if session is None:
        return EXIT_API
    ticket, password, port, _ = session

    session_id = _new_session_id()
    session_dir = config.console_session_log_dir
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    steps: list[dict[str, Any]] = []
    ok = False
    frame_width: int | None = None
    frame_height: int | None = None
    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            frame_width = vnc.width
            frame_height = vnc.height
            if points is None:
                max_x = max(0, (vnc.width or 1) - 1)
                max_y = max(0, (vnc.height or 1) - 1)
                points = [
                    (max_x // 4, max_y // 4),
                    (max_x // 2, max_y // 2),
                    ((max_x * 3) // 4, (max_y * 3) // 4),
                ]
            for idx, (x, y) in enumerate(points):
                clamped_x, clamped_y = vnc.clamp_pointer_coordinates(x, y)
                step_path = session_dir / f"{timestamp}-{vmid}-mouse-test-{idx + 1}.png"
                if not vnc.mouse_move(x, y):
                    raise ConnectionError(f"Failed to send VNC mouse move at step {idx + 1}")
                time.sleep(_effective_delay(args, config))
                screenshot_mode = _capture_after_input(vnc, step_path, vmid)
                steps.append({
                    "index": idx + 1,
                    "x": x,
                    "y": y,
                    "clamped_x": clamped_x,
                    "clamped_y": clamped_y,
                    "screenshot_mode": screenshot_mode,
                    "screenshot_path": str(step_path) if step_path.exists() else None,
                    "ok": True,
                })
            ok = True
    except Exception as exc:
        ok = False
        logger.log(
            event="console_mouse_test",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} steps={len(steps)} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_mouse_test",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 2,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "steps": steps,
        "ok": ok,
    }
    _save_session_event(config, session_event)
    _log_console_event(
        logger,
        "console_mouse_test",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "step_count": len(steps),
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to complete mouse test on VM console")
        result["data"] = {"node": node, "vmid": vmid, "steps": steps, "session_id": session_id}
        emit_output(result, args.format, title=f"Console Mouse-Test VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "steps": steps,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Mouse-Test VM {vmid}")
    return EXIT_SUCCESS

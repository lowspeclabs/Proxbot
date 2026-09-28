"""Tier 3 console commands: type, paste-text, install-preset, macro."""

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
from .mouse import _input_vnc_session
from .read import _ensure_vm_running
from ..protocol import (
    _SerialClient, _VNCClient, _WebSocketClient, _WebSocketFrame,
    _generate_websocket_key, _websocket_accept_key, _vnc_auth_response,
    _vnc_des_key,
)

def cmd_type(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Type",
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

    text = args.text
    if not text:
        result = _console_error_result("validation", "--text is required")
        emit_output(result, args.format, title="Console Type")
        return EXIT_VALIDATION

    if _type_text_has_shell_metacharacters(text) and not getattr(args, "unsafe", False):
        result = _console_error_result(
            "validation",
            "Text contains shell metacharacters. Use --unsafe only if you really intend to.",
            "The --unsafe flag must be explicitly set and the action is logged with a warning.",
        )
        emit_output(result, args.format, title="Console Type")
        return EXIT_VALIDATION

    running_check = _ensure_vm_running(args, config, client, logger, str(vmid))
    if running_check is not None and not running_check.get("ok"):
        result = _console_error_result(
            running_check.get("error_type", "console_error"),
            running_check.get("error", f"VM {vmid} is not running"),
        )
        emit_output(result, args.format, title=f"Console Type VM {vmid}")
        return EXIT_API

    snapshot_name = None
    if config.console_require_snapshot:
        recent_name, _ = _find_recent_snapshot(
            client, node, str(vmid), getattr(args, "snapshot", None), retention_minutes=60
        )
        if recent_name:
            snapshot_name = recent_name
        elif getattr(args, "auto_snapshot", False):
            snapshot_name = f"auto-before-console-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
            snap_endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
            snap_resp = client.post(snap_endpoint, {"snapname": snapshot_name})
            logger.log(
                event="console_snapshot_create",
                argv=sys.argv[1:],
                method="POST",
                endpoint=snap_endpoint,
                status_code=snap_resp.get("status_code"),
                duration_ms=snap_resp.get("duration_ms"),
                ok=snap_resp["ok"],
                response_summary=f"node={node} vmid={vmid} snapshot={snapshot_name}",
                error=snap_resp.get("error"),
            )
            if not snap_resp["ok"]:
                result = {"ok": False, "data": snap_resp.get("data"), "error": snap_resp.get("error")}
                emit_output(result, args.format, title=f"Console Type VM {vmid}")
                return EXIT_API
            task_id = cli._extract_task(snap_resp.get("data"))
            if task_id:
                wait_result = _wait_for_task(node, task_id, 120, client)
                if not wait_result.get("ok"):
                    emit_output(wait_result, args.format, title=f"Console Type VM {vmid}")
                    return EXIT_API
        else:
            result = _console_error_result(
                "safety_guard",
                "A recent snapshot is required before typing.",
                "Pass --auto-snapshot to create one, or create one with `snapshot` first.",
            )
            emit_output(result, args.format, title=f"Console Type VM {vmid}")
            return EXIT_SAFETY

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
        emit_output(result, args.format, title=f"Console Type VM {vmid}")
        return EXIT_API

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")
    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Type VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Type VM {vmid}")
        return EXIT_API

    session_id = _new_session_id()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"

    events = _text_to_events(text)
    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            if not _send_key_events(vnc, events):
                raise ConnectionError("Failed to send VNC text events")
            time.sleep(_effective_delay(args, config))
            png = vnc.request_screenshot(stable=True)
            if png is not None:
                _write_screenshot(png, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        ok = False
        logger.log(
            event="console_type",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} input_length={len(text)} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_type",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 3,
        "input_length": len(text),
        "snapshot": snapshot_name,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_type",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "input_length": len(text),
            "snapshot": snapshot_name,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to type text to VM console")
        emit_output(result, args.format, title=f"Console Type VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "input_length": len(text),
        "snapshot": snapshot_name,
        "session_id": session_id,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Type VM {vmid}")
    return EXIT_SUCCESS


def cmd_paste_text(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    """Type non-submitting text, retaining the snapshot and screenshot safeguards."""
    if "\r" in args.text or "\n" in args.text:
        result = _console_error_result(
            "validation",
            "paste-text cannot include Enter or carriage-return characters.",
            "Review the returned screenshot, then use send-key --key Enter separately if submission is intended.",
        )
        emit_output(result, args.format, title="Console Paste-Text")
        return EXIT_VALIDATION
    return cmd_type(args, config, client, logger)


def cmd_install_preset(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    """Run a versioned installer sequence only after the operator confirms its focus."""
    actions = INSTALL_PRESETS.get(args.preset)
    if actions is None:
        result = _console_error_result("validation", f"Unknown installer preset: {args.preset}")
        emit_output(result, args.format, title="Console Install Preset")
        return EXIT_VALIDATION
    if not args.tasksel_focus_confirmed:
        result = _console_error_result(
            "safety_guard",
            "Installer focus has not been confirmed.",
            "Take a screenshot and confirm the selected desktop task, then add --tasksel-focus-confirmed.",
        )
        emit_output(result, args.format, title="Console Install Preset")
        return EXIT_SAFETY
    args.actions = json.dumps(actions)
    args.file = None
    args.unsafe = False
    return cmd_macro(args, config, client, logger)


def cmd_macro(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Macro",
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

    actions_source = getattr(args, "actions", None)
    file_path = getattr(args, "file", None)
    raw_actions = None
    if actions_source:
        try:
            raw_actions = json.loads(actions_source)
        except json.JSONDecodeError as exc:
            result = _console_error_result("validation", f"Invalid --actions JSON: {exc}")
            emit_output(result, args.format, title="Console Macro")
            return EXIT_VALIDATION
    elif file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                raw_actions = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            result = _console_error_result("validation", f"Cannot read macro file: {exc}")
            emit_output(result, args.format, title="Console Macro")
            return EXIT_VALIDATION
    else:
        result = _console_error_result("validation", "Either --actions or --file is required")
        emit_output(result, args.format, title="Console Macro")
        return EXIT_VALIDATION

    if not isinstance(raw_actions, list):
        result = _console_error_result("validation", "Macro actions must be a JSON list")
        emit_output(result, args.format, title="Console Macro")
        return EXIT_VALIDATION

    allow_unsafe = getattr(args, "unsafe", False)
    validated_actions: list[tuple[str, dict[str, Any]]] = []
    for idx, action in enumerate(raw_actions):
        action_type, params = _validate_macro_action(action, allow_unsafe)
        if action_type == "":
            result = _console_error_result("validation", f"Invalid macro action at index {idx}: {action}")
            emit_output(result, args.format, title="Console Macro")
            return EXIT_VALIDATION
        validated_actions.append((action_type, params))

    running_check = _ensure_vm_running(args, config, client, logger, str(vmid))
    if running_check is not None and not running_check.get("ok"):
        result = _console_error_result(
            running_check.get("error_type", "console_error"),
            running_check.get("error", f"VM {vmid} is not running"),
        )
        emit_output(result, args.format, title=f"Console Macro VM {vmid}")
        return EXIT_API

    snapshot_name = None
    if config.console_require_snapshot:
        recent_name, _ = _find_recent_snapshot(
            client, node, str(vmid), getattr(args, "snapshot", None), retention_minutes=60
        )
        if recent_name:
            snapshot_name = recent_name
        elif getattr(args, "auto_snapshot", False):
            snapshot_name = f"auto-before-console-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
            snap_endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
            snap_resp = client.post(snap_endpoint, {"snapname": snapshot_name})
            logger.log(
                event="console_snapshot_create",
                argv=sys.argv[1:],
                method="POST",
                endpoint=snap_endpoint,
                status_code=snap_resp.get("status_code"),
                duration_ms=snap_resp.get("duration_ms"),
                ok=snap_resp["ok"],
                response_summary=f"node={node} vmid={vmid} snapshot={snapshot_name}",
                error=snap_resp.get("error"),
            )
            if not snap_resp["ok"]:
                result = {"ok": False, "data": snap_resp.get("data"), "error": snap_resp.get("error")}
                emit_output(result, args.format, title=f"Console Macro VM {vmid}")
                return EXIT_API
            task_id = cli._extract_task(snap_resp.get("data"))
            if task_id:
                wait_result = _wait_for_task(node, task_id, 120, client)
                if not wait_result.get("ok"):
                    emit_output(wait_result, args.format, title=f"Console Macro VM {vmid}")
                    return EXIT_API
        else:
            result = _console_error_result(
                "safety_guard",
                "A recent snapshot is required before running a macro.",
                "Pass --auto-snapshot to create one, or create one with `snapshot` first.",
            )
            emit_output(result, args.format, title=f"Console Macro VM {vmid}")
            return EXIT_SAFETY

    session = _input_vnc_session(node, config, client, logger, vmid, args)
    if session is None:
        return EXIT_API
    ticket, password, port, screenshot_path = session

    session_id = _new_session_id()
    executed: list[dict[str, Any]] = []
    ok = False
    error_msg = ""
    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            for idx, (action_type, params) in enumerate(validated_actions):
                if action_type == "key":
                    events = _key_name_to_events(params["key"])
                    if not events or not _send_key_events(vnc, events):
                        raise ConnectionError(f"Failed to send key at step {idx}")
                elif action_type == "keys":
                    for key in params["keys"]:
                        events = _key_name_to_events(key)
                        if not events or not _send_key_events(vnc, events):
                            raise ConnectionError(f"Failed to send keys at step {idx}")
                elif action_type == "type":
                    events = _text_to_events(params["text"])
                    if not events or not _send_key_events(vnc, events):
                        raise ConnectionError(f"Failed to type text at step {idx}")
                elif action_type == "click":
                    if not vnc.mouse_click(params["x"], params["y"], button=params["button"], clicks=params["clicks"]):
                        raise ConnectionError(f"Failed to click at step {idx}")
                elif action_type == "move":
                    if not vnc.mouse_move(params["x"], params["y"]):
                        raise ConnectionError(f"Failed to move mouse at step {idx}")
                elif action_type == "wait":
                    time.sleep(params["seconds"])
                executed.append({"index": idx, "action": action_type})
            time.sleep(_effective_delay(args, config))
            png = vnc.request_screenshot(stable=True)
            if png is not None:
                _write_screenshot(png, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        ok = False
        error_msg = str(exc)
        logger.log(
            event="console_macro",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} actions={len(validated_actions)} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_macro",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 3,
        "actions_count": len(validated_actions),
        "executed_count": len(executed),
        "snapshot": snapshot_name,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_macro",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "actions_count": len(validated_actions),
            "executed_count": len(executed),
            "snapshot": snapshot_name,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", f"Failed to run macro on VM console: {error_msg}")
        emit_output(result, args.format, title=f"Console Macro VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "actions_count": len(validated_actions),
        "executed_count": len(executed),
        "session_id": session_id,
        "snapshot": snapshot_name,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Macro VM {vmid}")
    return EXIT_SUCCESS

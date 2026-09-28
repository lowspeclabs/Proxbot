"""Tier 2 console input: send-key, send-keys, run-command."""

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

def cmd_send_key(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Send-Key",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    key = _canonical_key_token(args.key)
    if not _is_valid_key_token(key):
        result = _console_error_result(
            "validation",
            f"Key '{args.key}' is not allowed.",
            f"Allowed keys include: {', '.join(sorted(BASE_KEY_ALLOWLIST))} and combos such as {', '.join(sorted(COMMON_COMBO_ALLOWLIST))}",
        )
        emit_output(result, args.format, title="Console Send-Key")
        return EXIT_VALIDATION

    guard = require_execute(args, f"send-key to VM {vmid}")
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
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

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
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")
    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

    session_id = _new_session_id()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"

    events = _key_name_to_events(key)
    if not events:
        result = _console_error_result("console_error", f"Could not translate key '{key}' to VNC events")
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            if not _send_key_events(vnc, events):
                raise ConnectionError("Failed to send VNC key events")
            time.sleep(_effective_delay(args, config))
            png = vnc.request_screenshot(stable=True)
            if png is not None:
                _write_screenshot(png, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        ok = False
        logger.log(
            event="console_send_key",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} key={key} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_send_key",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 2,
        "key": key,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_send_key",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            "key": key,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to send key to VM console")
        emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "key": key,
        "session_id": session_id,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Send-Key VM {vmid}")
    return EXIT_SUCCESS


def cmd_send_keys(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Send-Keys",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    keys = _parse_key_sequence(args.keys)
    if keys is None:
        result = _console_error_result(
            "validation",
            f"Invalid key sequence '{args.keys}'.",
            f"Allowed keys include: {', '.join(sorted(BASE_KEY_ALLOWLIST))} and combos such as {', '.join(sorted(COMMON_COMBO_ALLOWLIST))}. Use comma-separated names; repeat with KeyxN (e.g., Downx10).",
        )
        emit_output(result, args.format, title="Console Send-Keys")
        return EXIT_VALIDATION
    key_summary = _key_sequence_summary(keys)

    guard = require_execute(args, f"send-keys to VM {vmid}")
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
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

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
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")
    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

    session_id = _new_session_id()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"

    events: list[tuple[int, int]] = []
    for key in keys:
        events.extend(_key_name_to_events(key))
    if not events:
        result = _console_error_result("console_error", "Could not translate key sequence to VNC events")
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            if not _send_key_events(vnc, events):
                raise ConnectionError("Failed to send VNC key events")
            time.sleep(_effective_delay(args, config))
            png = vnc.request_screenshot(stable=True)
            if png is not None:
                _write_screenshot(png, screenshot_path, vmid)
            ok = True
    except Exception as exc:
        ok = False
        logger.log(
            event="console_send_keys",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} key_count={key_summary['key_count']} error={exc}",
        )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_send_keys",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 2,
        **key_summary,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ok": ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_send_keys",
        args,
        ok=ok,
        extra={
            "session_id": session_id,
            **key_summary,
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "ticket": REDACTED,
        },
    )

    if not ok:
        result = _console_error_result("console_error", "Failed to send keys to VM console")
        emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
        return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        **key_summary,
        "session_id": session_id,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Send-Keys VM {vmid}")
    return EXIT_SUCCESS


def cmd_run_command(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Run-Command",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    command = args.command.strip()
    if not command:
        result = _console_error_result("validation", "--command is required")
        emit_output(result, args.format, title="Console Run-Command")
        return EXIT_VALIDATION

    if command not in RUN_COMMAND_ALLOWLIST:
        result = _console_error_result(
            "validation",
            f"Command '{command}' is not in the run-command allowlist.",
            "Contact the administrator to add safe diagnostics to the allowlist.",
        )
        emit_output(result, args.format, title="Console Run-Command")
        return EXIT_VALIDATION

    guard = require_execute(args, f"run-command on VM {vmid}")
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
        emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
        return EXIT_API

    cmd_parts = command.split()
    body = {"command": cmd_parts[0], "capture-output": 1}
    if len(cmd_parts) > 1:
        body["arg"] = cmd_parts[1:]

    exec_endpoint = f"/nodes/{node}/qemu/{vmid}/agent/exec"
    exec_resp = client.post(exec_endpoint, body)
    logger.log(
        event="console_agent_exec",
        argv=sys.argv[1:],
        method="POST",
        endpoint=exec_endpoint,
        status_code=exec_resp.get("status_code"),
        duration_ms=exec_resp.get("duration_ms"),
        ok=exec_resp["ok"],
        response_summary=f"node={node} vmid={vmid} command={cmd_parts[0]}",
        error=exec_resp.get("error"),
    )
    if not exec_resp["ok"]:
        result = {"ok": False, "data": exec_resp.get("data"), "error": exec_resp.get("error")}
        emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
        return EXIT_API

    exec_data = exec_resp.get("data") or {}
    pid = exec_data.get("pid")
    if pid is None:
        result = _console_error_result("console_error", "QEMU guest agent did not return a PID")
        emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
        return EXIT_API

    status_endpoint = f"/nodes/{node}/qemu/{vmid}/agent/exec-status"
    deadline = time.time() + min(30, config.timeout_seconds)
    output = {"out-data": "", "err-data": "", "exited": False}
    while time.time() < deadline:
        status_resp = client.post(status_endpoint, {"pid": pid})
        logger.log(
            event="console_agent_exec_status",
            argv=sys.argv[1:],
            method="POST",
            endpoint=status_endpoint,
            status_code=status_resp.get("status_code"),
            duration_ms=status_resp.get("duration_ms"),
            ok=status_resp["ok"],
            response_summary=f"node={node} vmid={vmid} pid={pid}",
            error=status_resp.get("error"),
        )
        if not status_resp["ok"]:
            result = {"ok": False, "data": status_resp.get("data"), "error": status_resp.get("error")}
            emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
            return EXIT_API
        status_data = status_resp.get("data") or {}
        if status_data.get("exited"):
            output = status_data
            break
        time.sleep(1)
    else:
        result = _console_error_result("timeout", f"Timed out waiting for command PID {pid} to exit")
        emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
        return EXIT_TIMEOUT

    session_id = _new_session_id()
    _log_console_event(
        logger,
        "console_run_command",
        args,
        ok=True,
        extra={
            "session_id": session_id,
            "command": command,
            "pid": pid,
            "exit_code": output.get("exitcode"),
        },
    )

    out_data = output.get("out-data", "")
    err_data = output.get("err-data", "")
    if isinstance(out_data, str):
        try:
            out_text = base64.b64decode(out_data).decode("utf-8", errors="replace")
        except Exception:
            out_text = out_data
    else:
        out_text = str(out_data)
    if isinstance(err_data, str):
        try:
            err_text = base64.b64decode(err_data).decode("utf-8", errors="replace")
        except Exception:
            err_text = err_data
    else:
        err_text = str(err_data)

    data = {
        "node": node,
        "vmid": vmid,
        "command": command,
        "pid": pid,
        "exit_code": output.get("exitcode"),
        "stdout": out_text[:2000],
        "stderr": err_text[:2000],
        "session_id": session_id,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Run-Command VM {vmid}")
    return EXIT_SUCCESS

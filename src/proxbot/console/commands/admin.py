"""Snapshot and console session management commands."""

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
    _wait_for_task, _write_screenshot,
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
from ..protocol import (
    _SerialClient, _VNCClient, _WebSocketClient, _WebSocketFrame,
    _generate_websocket_key, _websocket_accept_key, _vnc_auth_response,
    _vnc_des_key,
)

def cmd_snapshot(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
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
            title="Console Snapshot",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    name = args.name
    guard = require_execute(args, f"create snapshot on VM {vmid}")
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

    endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
    resp = client.post(endpoint, {"snapname": name})
    task_id = cli._extract_task(resp.get("data"))
    logger.log(
        event="console_snapshot_create",
        argv=sys.argv[1:],
        method="POST",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid} snapshot={name} task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    if not resp["ok"]:
        result = {"ok": False, "data": resp.get("data"), "error": resp.get("error")}
        emit_output(result, args.format, title=f"Console Snapshot VM {vmid}")
        return EXIT_API

    if task_id and getattr(args, "wait", False):
        wait_result = _wait_for_task(node, task_id, getattr(args, "timeout", 120), client)
        if not wait_result.get("ok"):
            emit_output(wait_result, args.format, title=f"Console Snapshot VM {vmid}")
            return EXIT_API

    data = {
        "node": node,
        "vmid": vmid,
        "snapshot": name,
        "task_id": task_id,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Snapshot VM {vmid}")
    return EXIT_SUCCESS


def cmd_session(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    vmid = args.vmid
    session_dir = config.console_session_log_dir

    if getattr(args, "list", False):
        files = sorted(_session_files(config, vmid), key=lambda p: p.name, reverse=True)
        data = {
            "node": node,
            "vmid": vmid,
            "sessions": [
                {
                    "name": p.name,
                    "path": str(p),
                    "size": p.stat().st_size,
                }
                for p in files
            ],
        }
        result = {"ok": True, "data": data, "error": None}
        emit_output(result, args.format, title=f"Console Sessions VM {vmid}")
        return EXIT_SUCCESS

    if getattr(args, "stop", False):
        session_id = args.session_id
        if not session_id:
            result = _console_error_result("validation", "--session-id is required for --stop")
            emit_output(result, args.format, title="Console Session Stop")
            return EXIT_VALIDATION

        guard = require_destructive(args, session_id)
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

        target = session_dir / session_id
        if not target.exists() or not target.is_file():
            result = _console_error_result("not_found", f"Session file not found: {session_id}")
            emit_output(result, args.format, title="Console Session Stop")
            return EXIT_NOOP

        try:
            target.unlink()
        except OSError as exc:
            result = _console_error_result("io_error", f"Failed to delete session file: {exc}")
            emit_output(result, args.format, title="Console Session Stop")
            return EXIT_API

        logger.log(
            event="console_session_stop",
            argv=sys.argv[1:],
            ok=True,
            response_summary=f"node={node} vmid={vmid} session={session_id}",
        )
        data = {"node": node, "vmid": vmid, "session_id": session_id, "stopped": True}
        result = {"ok": True, "data": data, "error": None}
        emit_output(result, args.format, title="Console Session Stop")
        return EXIT_SUCCESS

    result = _console_error_result("validation", "Use --list or --stop for session management")
    emit_output(result, args.format, title="Console Session")
    return EXIT_VALIDATION

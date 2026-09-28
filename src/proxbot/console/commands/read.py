"""Tier 1 read-only console commands: doctor, status, screenshot, read-text, watch."""

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
from ..protocol import (
    _SerialClient, _VNCClient, _WebSocketClient, _WebSocketFrame,
    _generate_websocket_key, _websocket_accept_key, _vnc_auth_response,
    _vnc_des_key,
)

def cmd_doctor(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    data: dict[str, Any] = {
        ".env_found": cli.ENV_PATH.exists(),
        "api_url": config.api_url or REDACTED,
        "token_id": config.token_id or REDACTED,
        "token_secret": REDACTED if config.token_secret else "missing",
        "ssl_verification": "enabled" if config.verify_ssl else "disabled (WARNING)",
        "log_directory": str(config.log_dir),
        "console_allowed_vmids": config.console_allowed_vmids or "none (all console operations blocked)",
        "console_tier": config.console_tier,
        "console_require_snapshot": config.console_require_snapshot,
        "console_key_delay": config.console_key_delay,
        "console_watch_interval": config.console_watch_interval,
        "console_session_log_dir": str(config.console_session_log_dir),
    }
    if not config.ok:
        data["config_status"] = "missing required values: " + ", ".join(config.missing())
        result = _console_error_result("config", data["config_status"])
        result["data"] = data
        emit_output(result, args.format, title="Console Doctor")
        return EXIT_CONFIG

    if not config.console_allowed_vmids:
        data["console_status"] = "blocked: PROXMOX_CONSOLE_ALLOWED_VMIDS is empty"
        result = _console_error_result("safety", data["console_status"])
        result["data"] = data
        emit_output(result, args.format, title="Console Doctor")
        logger.log(
            event="console_doctor",
            argv=sys.argv[1:],
            ok=False,
            response_summary="console_allowed_vmids empty",
        )
        return EXIT_SAFETY

    resp = client.get("/version")
    data["api_connectivity"] = "ok" if resp["ok"] else "failed"
    version_data = resp.get("data") or {}
    summary = f"version={version_data.get('version', 'unknown')}"
    if not resp["ok"]:
        data["error"] = str(resp.get("error", "unknown"))
    logger.log(
        event="console_doctor",
        argv=sys.argv[1:],
        method="GET",
        endpoint="/version",
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=summary,
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": data, "error": resp.get("error")}
    emit_output(result, args.format, title="Console Doctor")
    if not resp["ok"]:
        return EXIT_API
    return EXIT_SUCCESS


def cmd_status(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 1)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Status",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    endpoint = f"/nodes/{node}/qemu/{vmid}/status/current"
    resp = client.get(endpoint)
    logger.log(
        event="console_status",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid}",
        error=resp.get("error"),
    )

    if not resp["ok"]:
        result = {"ok": False, "data": resp.get("data"), "error": resp.get("error")}
        emit_output(result, args.format, title=f"Console Status VM {vmid}")
        return EXIT_API if resp.get("status_code") not in (401, 403) else EXIT_AUTH

    vm_data = resp.get("data") or {}

    snap_endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
    snap_resp = client.get(snap_endpoint)
    logger.log(
        event="console_snapshot_list",
        argv=sys.argv[1:],
        method="GET",
        endpoint=snap_endpoint,
        status_code=snap_resp.get("status_code"),
        duration_ms=snap_resp.get("duration_ms"),
        ok=snap_resp["ok"],
        response_summary=f"node={node} vmid={vmid}",
        error=snap_resp.get("error"),
    )
    snapshots = snap_resp.get("data") if snap_resp["ok"] else []
    recent_snapshots = []
    if isinstance(snapshots, list):
        recent_snapshots = [
            s for s in snapshots
            if isinstance(s, dict) and str(s.get("name", "")).startswith("auto-before-console-")
        ]

    sessions = _session_files(config, vmid)

    data = {
        "node": node,
        "vmid": vmid,
        "status": vm_data.get("qmpstatus", vm_data.get("status", "unknown")),
        "console_allowed": True,
        "tier": config.console_tier,
        "active_sessions": len(sessions),
        "recent_auto_snapshots": [s.get("name") for s in recent_snapshots[:5]],
        "session_log_dir": str(config.console_session_log_dir),
    }

    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Status VM {vmid}")
    return EXIT_SUCCESS


def _ensure_vm_running(
    args: argparse.Namespace,
    config: ConsoleConfig,
    client: ProxmoxClient,
    logger: Logger,
    vmid: str | int,
) -> dict[str, Any] | None:
    status_endpoint = f"/nodes/{args.node}/qemu/{vmid}/status/current"
    status_resp = client.get(status_endpoint)
    logger.log(
        event="console_status",
        argv=sys.argv[1:],
        method="GET",
        endpoint=status_endpoint,
        status_code=status_resp.get("status_code"),
        duration_ms=status_resp.get("duration_ms"),
        ok=status_resp["ok"],
        response_summary=f"node={args.node} vmid={vmid}",
        error=status_resp.get("error"),
    )
    if not status_resp["ok"]:
        return status_resp
    vm_data = status_resp.get("data") or {}
    if vm_data.get("qmpstatus", vm_data.get("status")) != "running":
        return {"ok": False, "error": f"VM {vmid} is not running", "error_type": "console_error"}
    return None


def cmd_screenshot(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 1)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Screenshot",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    running_check = _ensure_vm_running(args, config, client, logger, vmid)
    if running_check is not None:
        if not running_check.get("ok"):
            result = _console_error_result(
                running_check.get("error_type", "console_error"),
                running_check.get("error", f"VM {vmid} is not running"),
            )
            emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
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
        emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
        return EXIT_API

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")

    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
        return EXIT_API

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"
    session_id = _new_session_id()

    capture_ok = _capture_vnc_screenshot(
        config, node, str(vmid), ticket, port, screenshot_path, config.timeout_seconds, password=password
    )

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_screenshot",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 1,
        "screenshot_path": str(screenshot_path),
        "ok": capture_ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_screenshot",
        args,
        ok=capture_ok,
        extra={
            "session_id": session_id,
            "screenshot_path": str(screenshot_path),
            "ticket": REDACTED,
        },
    )

    if capture_ok:
        data = {
            "node": node,
            "vmid": vmid,
            "screenshot_path": str(screenshot_path),
            "format": "png",
            "session_id": session_id,
            "session_log_dir": str(session_dir),
            "ticket": REDACTED,
        }
        result = {"ok": True, "data": data, "error": None}
        emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
        return EXIT_SUCCESS

    ws_url = f"{config.api_url.replace('/api2/json', '')}/nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={ticket}"
    redacted_url = ws_url.replace(ticket, REDACTED)
    result = _console_error_result(
        "console_error",
        "Screenshot capture failed.",
        "The VNC ticket was obtained, but the websocket capture could not be completed. "
        "This may be because the VNC server closed the connection or uses an unsupported encoding. "
        "Connect manually with a VNC viewer if needed.",
    )
    result["data"] = {
        "node": node,
        "vmid": vmid,
        "vnc_port": port,
        "websocket_url": redacted_url,
        "session_id": session_id,
    }
    emit_output(result, args.format, title=f"Console Screenshot VM {vmid}")
    return EXIT_API


def cmd_read_text(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 1)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Read-Text",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    running_check = _ensure_vm_running(args, config, client, logger, vmid)
    if running_check is not None:
        if not running_check.get("ok"):
            result = _console_error_result(
                running_check.get("error_type", "console_error"),
                running_check.get("error", f"VM {vmid} is not running"),
            )
            emit_output(result, args.format, title=f"Console Read-Text VM {vmid}")
            return EXIT_API

    config_endpoint = f"/nodes/{node}/qemu/{vmid}/config"
    config_resp = client.get(config_endpoint)
    logger.log(
        event="console_config",
        argv=sys.argv[1:],
        method="GET",
        endpoint=config_endpoint,
        status_code=config_resp.get("status_code"),
        duration_ms=config_resp.get("duration_ms"),
        ok=config_resp["ok"],
        response_summary=f"node={node} vmid={vmid}",
        error=config_resp.get("error"),
    )

    has_serial = False
    if config_resp["ok"]:
        vm_config = config_resp.get("data") or {}
        for key in vm_config:
            if key.startswith("serial"):
                has_serial = True
                break

    termproxy_endpoint = f"/nodes/{node}/qemu/{vmid}/termproxy"
    termproxy_resp = client.post(termproxy_endpoint, {})
    logger.log(
        event="console_termproxy",
        argv=sys.argv[1:],
        method="POST",
        endpoint=termproxy_endpoint,
        status_code=termproxy_resp.get("status_code"),
        duration_ms=termproxy_resp.get("duration_ms"),
        ok=termproxy_resp["ok"],
        response_summary=f"node={node} vmid={vmid}",
        error=termproxy_resp.get("error"),
    )

    if not termproxy_resp["ok"]:
        result = _console_error_result(
            "console_error",
            "Could not obtain serial console ticket.",
            "Ensure the VM has a serial device and the API token has VM.Console permission.",
        )
        result["data"] = {"has_serial_device": has_serial}
        emit_output(result, args.format, title=f"Console Read-Text VM {vmid}")
        return EXIT_API

    term_data = termproxy_resp.get("data") or {}
    ticket = term_data.get("ticket")
    port = term_data.get("port", "5900")

    if not ticket:
        result = _console_error_result("console_error", "No serial console ticket returned")
        result["data"] = {"has_serial_device": has_serial}
        emit_output(result, args.format, title=f"Console Read-Text VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Read-Text VM {vmid}")
        return EXIT_API

    session_id = _new_session_id()
    text = _capture_serial_text(config, node, str(vmid), ticket, port, config.timeout_seconds)

    text_path = session_dir / f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{vmid}.txt"
    try:
        text_path.write_bytes(text)
    except OSError:
        pass

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_read_text",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 1,
        "text_path": str(text_path),
        "text_length": len(text),
        "ok": True,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_read_text",
        args,
        ok=True,
        extra={
            "session_id": session_id,
            "text_path": str(text_path),
            "text_length": len(text),
            "ticket": REDACTED,
        },
    )

    ws_url = f"{config.api_url.replace('/api2/json', '')}/nodes/{node}/qemu/{vmid}/termwebsocket?port={port}&termproxy={ticket}"
    redacted_url = ws_url.replace(ticket, REDACTED)
    data = {
        "node": node,
        "vmid": vmid,
        "has_serial_device": has_serial,
        "text_length": len(text),
        "text_preview": text[:200].decode("utf-8", errors="replace"),
        "text_path": str(text_path),
        "session_id": session_id,
        "websocket_url": redacted_url,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Read-Text VM {vmid}")
    return EXIT_SUCCESS


def cmd_watch(args: argparse.Namespace, config: ConsoleConfig, client: ProxmoxClient, logger: Logger) -> int:
    node = _require_node(args, config, logger)
    if node is None:
        return EXIT_VALIDATION

    eligibility = _check_eligibility(args, config, logger, 1)
    if eligibility is not None:
        emit_output(
            {
                "ok": False,
                "error_type": eligibility["error_type"],
                "error": eligibility["error"],
                "hint": eligibility["hint"],
            },
            args.format,
            title="Console Watch",
        )
        return EXIT_SAFETY

    vmid = args.vmid
    running_check = _ensure_vm_running(args, config, client, logger, vmid)
    if running_check is not None:
        if not running_check.get("ok"):
            result = _console_error_result(
                running_check.get("error_type", "console_error"),
                running_check.get("error", f"VM {vmid} is not running"),
            )
            emit_output(result, args.format, title=f"Console Watch VM {vmid}")
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
        emit_output(result, args.format, title=f"Console Watch VM {vmid}")
        return EXIT_API

    vnc_data = vnc_resp.get("data") or {}
    ticket = vnc_data.get("ticket")
    password = vnc_data.get("password")
    port = vnc_data.get("port", "5900")
    if not ticket:
        result = _console_error_result("console_error", "No VNC ticket returned by Proxmox")
        emit_output(result, args.format, title=f"Console Watch VM {vmid}")
        return EXIT_API

    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _console_error_result("io_error", f"Cannot create session log directory: {exc}")
        emit_output(result, args.format, title=f"Console Watch VM {vmid}")
        return EXIT_API

    session_id = _new_session_id()
    interval = getattr(args, "interval", None)
    if interval is None or interval <= 0:
        interval = config.console_watch_interval
    timeout = getattr(args, "timeout", None)
    if timeout is None or timeout <= 0:
        timeout = config.timeout_seconds
    until_stable = getattr(args, "until_stable", False)
    stable_required = max(1, int(getattr(args, "stable_cycles", 2)))
    unchanged_for = getattr(args, "until_unchanged_for", None) or 0.0
    if unchanged_for < 0:
        unchanged_for = 0.0
    save_screenshots = getattr(args, "screenshots", False)

    start = time.time()
    deadline = start + timeout
    previous_png: bytes | None = None
    stable_count = 0
    iterations = 0
    last_change_time = start
    last_screenshot_path: pathlib.Path | None = None
    last_png: bytes | None = None
    capture_ok = False
    summary = "watch completed"

    try:
        with _vnc_session(config, node, str(vmid), ticket, port, config.timeout_seconds, password=password) as vnc:
            while time.time() < deadline:
                iterations += 1
                png = vnc.request_screenshot(stable=False)
                if png is None:
                    break
                last_png = png

                if save_screenshots:
                    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    screenshot_path = session_dir / f"{timestamp}-{vmid}.png"
                    _write_screenshot(png, screenshot_path, vmid)
                    last_screenshot_path = screenshot_path

                if until_stable or unchanged_for:
                    if previous_png is not None and png == previous_png:
                        stable_count += 1
                        stable_duration = time.time() - last_change_time
                        if stable_count >= stable_required and (not unchanged_for or stable_duration >= unchanged_for):
                            capture_ok = True
                            summary = f"screen stable for {stable_count} consecutive captures ({stable_duration:.1f}s)"
                            break
                    else:
                        stable_count = 0
                        last_change_time = time.time()
                    previous_png = png
                else:
                    capture_ok = True
                    summary = f"captured {iterations} screenshot(s)"
                    break

                time.sleep(interval)
    except Exception as exc:
        logger.log(
            event="console_watch",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"node={node} vmid={vmid} error={exc}",
        )

    if not capture_ok and last_png is not None and last_screenshot_path is None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        last_screenshot_path = session_dir / f"{timestamp}-{vmid}-watch-timeout.png"
        _write_screenshot(last_png, last_screenshot_path, vmid)

    seconds_since_change = max(0.0, time.time() - last_change_time)

    session_event = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "event": "console_watch",
        "node": node,
        "vmid": vmid,
        "session_id": session_id,
        "tier": 1,
        "iterations": iterations,
        "stable": capture_ok and until_stable,
        "stable_count": stable_count,
        "seconds_since_change": round(seconds_since_change, 1),
        "screenshot_path": str(last_screenshot_path) if last_screenshot_path else None,
        "ok": capture_ok,
    }
    _save_session_event(config, session_event)

    _log_console_event(
        logger,
        "console_watch",
        args,
        ok=capture_ok,
        extra={
            "session_id": session_id,
            "iterations": iterations,
            "stable_count": stable_count,
            "seconds_since_change": round(seconds_since_change, 1),
            "summary": summary,
            "screenshot_path": str(last_screenshot_path) if last_screenshot_path else None,
            "ticket": REDACTED,
        },
    )

    if not capture_ok:
        result = _console_error_result(
            "console_error",
            "Watch did not reach a stable screen before timeout.",
            "The VM may still be animating, or the VNC session may have closed. Try increasing --timeout or --interval.",
        )
        result["data"] = {
            "node": node,
            "vmid": vmid,
            "iterations": iterations,
            "stable_count": stable_count,
            "seconds_since_change": round(seconds_since_change, 1),
            "screenshot_path": str(last_screenshot_path) if last_screenshot_path else None,
            "session_id": session_id,
        }
        emit_output(result, args.format, title=f"Console Watch VM {vmid}")
        return EXIT_TIMEOUT

    data = {
        "node": node,
        "vmid": vmid,
        "iterations": iterations,
        "stable_count": stable_count,
        "seconds_since_change": round(seconds_since_change, 1),
        "summary": summary,
        "screenshot_path": str(last_screenshot_path) if last_screenshot_path else None,
        "session_id": session_id,
        "ticket": REDACTED,
    }
    result = {"ok": True, "data": data, "error": None}
    emit_output(result, args.format, title=f"Console Watch VM {vmid}")
    return EXIT_SUCCESS

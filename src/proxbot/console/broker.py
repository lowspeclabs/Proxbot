"""Console broker core: config, eligibility guards, sessions, and capture helpers."""

import argparse
import base64
import contextlib
import datetime
import hashlib
import importlib.util
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

from ..common.config import Config
from ..common.constants import (
    EXIT_API, EXIT_AUTH, EXIT_CONFIG, EXIT_INTERNAL, EXIT_NOOP, EXIT_SAFETY,
    EXIT_SUCCESS, EXIT_TIMEOUT, EXIT_VALIDATION, REDACTED, REPO_ROOT,
)
from ..common.logging_ import Logger
from ..common.client import ProxmoxClient
from ..common.redact import redact_string
from ..common.render import emit_error, emit_output, render_output
from ..common.safety import require_destructive, require_execute
from .protocol import _SerialClient, _VNCClient, _WebSocketClient

# The main CLI stays the single source of truth for shared plumbing.
CLI_PATH = REPO_ROOT / "scripts" / "Proxmox-cli.py"
_spec = importlib.util.spec_from_file_location("proxmox_cli", str(CLI_PATH))
cli = importlib.util.module_from_spec(_spec)
sys.modules["proxmox_cli"] = cli
_spec.loader.exec_module(cli)

DEFAULT_CONSOLE_SESSION_LOG_DIR = REPO_ROOT / "scripts" / "logs" / "console-sessions"
DEFAULT_CONSOLE_KEY_DELAY = 1.5
DEFAULT_CONSOLE_WATCH_INTERVAL = 5.0

def _find_recent_snapshot(
    client: ProxmoxClient,
    node: str,
    vmid: str | int,
    name: str | None = None,
    retention_minutes: int = 60,
) -> tuple[str | None, dict[str, Any]]:
    """Return (snapshot_name, api_response) for a matching recent snapshot."""
    endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
    resp = client.get(endpoint)
    if not resp["ok"]:
        return None, resp
    snapshots = resp.get("data") or []
    if not isinstance(snapshots, list):
        return None, resp

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        snap_name = str(snap.get("name", ""))
        if name and snap_name == name:
            return snap_name, resp
        if not name and snap_name.startswith("auto-before-console-"):
            snap_time = snap.get("snaptime")
            if snap_time is not None:
                try:
                    snap_dt = datetime.datetime.fromtimestamp(snap_time, tz=datetime.timezone.utc)
                    if (now - snap_dt).total_seconds() <= retention_minutes * 60:
                        return snap_name, resp
                except (TypeError, OSError, ValueError):
                    pass
            else:
                return snap_name, resp
    return None, resp


def _wait_for_task(
    node: str,
    upid: str,
    timeout: int,
    client: ProxmoxClient,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    poll_interval = 2
    while time.time() < deadline:
        endpoint = f"/nodes/{node}/tasks/{upid}/status"
        resp = client.get(endpoint)
        if not resp["ok"]:
            return resp
        status_data = resp.get("data", {}) or {}
        if status_data.get("status") == "stopped":
            exitstatus = status_data.get("exitstatus", "unknown")
            ok = exitstatus == "OK"
            return {"ok": ok, "data": status_data, "error": None if ok else f"Task failed: {exitstatus}"}
        time.sleep(poll_interval)
    return {"ok": False, "data": None, "error": f"Timeout waiting for task {upid}"}


class ConsoleConfig(Config):
    def __init__(self, raw: dict[str, str]):
        super().__init__(raw)
        self.console_allowed_vmids = self._parse_vmids(raw.get("PROXMOX_CONSOLE_ALLOWED_VMIDS", ""))
        self.console_tier = self._parse_tier(raw.get("PROXMOX_CONSOLE_TIER", "1"))
        self.console_require_snapshot = self._parse_bool(raw.get("PROXMOX_CONSOLE_REQUIRE_SNAPSHOT", "true"))
        self.console_key_delay = self._parse_float(raw.get("PROXMOX_CONSOLE_KEY_DELAY"), DEFAULT_CONSOLE_KEY_DELAY)
        self.console_watch_interval = self._parse_float(raw.get("PROXMOX_CONSOLE_WATCH_INTERVAL"), DEFAULT_CONSOLE_WATCH_INTERVAL)
        default_session_dir = self.log_dir / "console-sessions"
        self.console_session_log_dir = pathlib.Path(raw.get("PROXMOX_CONSOLE_SESSION_LOG_DIR") or default_session_dir)
        try:
            self.console_session_log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _parse_vmids(self, value: str | None) -> list[int]:
        if not value:
            return []
        return [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]

    def _parse_tier(self, value: str | None) -> int:
        if value is None:
            return 1
        try:
            tier = int(value.strip())
            return max(1, min(4, tier))
        except ValueError:
            return 1

    def _parse_float(self, value: str | None, default: float) -> float:
        if value is None:
            return default
        try:
            return max(0.0, float(value.strip()))
        except ValueError:
            return default

    def _parse_bool(self, value: str | None) -> bool:
        if value is None:
            return False
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def is_console_allowed(self, vmid: int | str) -> bool:
        if not self.console_allowed_vmids:
            return False
        try:
            return int(vmid) in self.console_allowed_vmids
        except ValueError:
            return False


def _effective_delay(args: argparse.Namespace, config: ConsoleConfig) -> float:
    """Return the key delay from --delay flag, env var, or default."""
    delay = getattr(args, "delay", None)
    if delay is not None:
        return max(0.0, float(delay))
    return config.console_key_delay


def _console_error_result(error_type: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error_type": error_type, "error": message, "hint": hint}


def _check_emergency_stop(config: ConsoleConfig) -> dict[str, Any] | None:
    """Return an error result if the emergency STOP file exists."""
    stop_file = (config.log_dir or pathlib.Path("scripts/logs")) / "STOP"
    if stop_file.exists():
        return _console_error_result(
            "safety_guard",
            f"Emergency stop file exists: {stop_file}",
            "Remove the STOP file to resume console input actions.",
        )
    return None


def _session_files(config: ConsoleConfig, vmid: str | int) -> list[pathlib.Path]:
    if not config.console_session_log_dir.exists():
        return []
    pattern = re.compile(rf"^\d{{8}}-\d{{6}}-{vmid}\.")
    return [p for p in config.console_session_log_dir.iterdir() if p.is_file() and pattern.match(p.name)]


def _check_eligibility(
    args: argparse.Namespace,
    config: ConsoleConfig,
    logger: Logger,
    required_tier: int,
) -> dict[str, Any] | None:
    vmid = getattr(args, "vmid", None)
    if vmid is None:
        return _console_error_result("validation", "--vmid is required")
    if not config.is_console_allowed(vmid):
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"vmid={vmid} not in PROXMOX_CONSOLE_ALLOWED_VMIDS",
        )
        return _console_error_result(
            "safety_guard",
            f"VMID {vmid} is not allowed for console access.",
            "Add it to PROXMOX_CONSOLE_ALLOWED_VMIDS in .env.",
        )
    if config.console_tier < required_tier:
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            ok=False,
            response_summary=f"tier {required_tier} required, configured tier {config.console_tier}",
        )
        return _console_error_result(
            "safety_guard",
            f"Console tier {required_tier} required, but PROXMOX_CONSOLE_TIER is set to {config.console_tier}.",
            f"Raise PROXMOX_CONSOLE_TIER to at least {required_tier} to use this command.",
        )
    return None


def _require_node(args: argparse.Namespace, config: ConsoleConfig, logger: Logger) -> str | None:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return None
    return node


def _log_console_event(
    logger: Logger,
    event: str,
    args: argparse.Namespace,
    ok: bool,
    extra: dict[str, Any] | None = None,
    snapshot: str | None = None,
    session_id: str | None = None,
) -> None:
    entry_extra: dict[str, Any] = {"node": getattr(args, "node", None), "vmid": getattr(args, "vmid", None)}
    if snapshot:
        entry_extra["snapshot"] = snapshot
    if session_id:
        entry_extra["session_id"] = session_id
    if extra:
        entry_extra.update(extra)
    logger.log(
        event=event,
        argv=sys.argv[1:],
        ok=ok,
        extra=entry_extra,
    )


def _parse_api_url_parts(config: ConsoleConfig) -> tuple[str, int, bool]:
    url = config.api_url
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_ssl = parsed.scheme == "https"
    return host, port, use_ssl


def _vnc_session(
    config: ConsoleConfig,
    node: str,
    vmid: str,
    ticket: str,
    port: str | int,
    timeout: int,
    password: str | None = None,
):
    """Context manager that yields a connected _VNCClient, closing on exit."""
    host, api_port, use_ssl = _parse_api_url_parts(config)
    ssl_context = ssl.create_default_context()
    if not config.verify_ssl:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    quoted_ticket = urllib.parse.quote(ticket, safe="")
    path = f"/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={quoted_ticket}"
    headers = {"Authorization": config.auth_header()}

    ws = _WebSocketClient(host, api_port, use_ssl, ssl_context, timeout)
    vnc: _VNCClient | None = None
    try:
        if not ws.connect(path, headers):
            raise ConnectionError("Failed to establish VNC websocket connection")
        vnc = _VNCClient(ws)
        if not vnc.connect(password=password or ticket):
            raise ConnectionError("Failed to complete VNC handshake")
        yield vnc
    finally:
        ws.close()


def _capture_vnc_screenshot(
    config: ConsoleConfig,
    node: str,
    vmid: str,
    ticket: str,
    port: str | int,
    output_path: pathlib.Path,
    timeout: int,
    password: str | None = None,
) -> bool:
    try:
        with _vnc_session(config, node, vmid, ticket, port, timeout, password=password) as vnc:
            png = vnc.request_screenshot(stable=True)
            if png is None:
                return False
            _write_screenshot(png, output_path, vmid)
            return True
    except Exception:
        return False


def _write_screenshot(png: bytes, screenshot_path: pathlib.Path, vmid: str | int) -> None:
    """Write a PNG screenshot to the session log dir and a /tmp review copy."""
    screenshot_path.write_bytes(png)
    try:
        review_path = pathlib.Path(f"/tmp/console-{vmid}-{screenshot_path.name}")
        review_path.write_bytes(png)
    except OSError:
        pass


def _capture_after_input(vnc: "_VNCClient", screenshot_path: pathlib.Path, vmid: str | int) -> str | None:
    """Capture an after-action screenshot, falling back if stable capture fails."""
    png = vnc.request_screenshot(stable=True)
    mode = "stable"
    if png is None:
        png = vnc.request_screenshot(stable=False)
        mode = "fallback"
    if png is None:
        return None
    _write_screenshot(png, screenshot_path, vmid)
    return mode


def _capture_serial_text(
    config: ConsoleConfig,
    node: str,
    vmid: str,
    ticket: str,
    port: str | int,
    timeout: int,
) -> bytes:
    host, api_port, use_ssl = _parse_api_url_parts(config)
    ssl_context = ssl.create_default_context()
    if not config.verify_ssl:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    quoted_ticket = urllib.parse.quote(ticket, safe="")
    path = f"/api2/json/nodes/{node}/qemu/{vmid}/termwebsocket?port={port}&termproxy={quoted_ticket}"
    headers = {"Authorization": config.auth_header()}

    ws = _WebSocketClient(host, api_port, use_ssl, ssl_context, timeout)
    try:
        if not ws.connect(path, headers):
            return b""
        serial = _SerialClient(ws)
        return serial.read_available(timeout=min(timeout, 5.0))
    finally:
        ws.close()


def _save_session_event(config: ConsoleConfig, event: dict[str, Any]) -> None:
    session_dir = config.console_session_log_dir
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = session_dir / f"{stamp}-{event.get('vmid', 'unknown')}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
    except OSError:
        pass


def _new_session_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = os.urandom(4).hex()
    return f"sess-{stamp}-{rand}"

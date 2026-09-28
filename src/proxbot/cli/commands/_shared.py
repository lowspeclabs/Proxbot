"""Cross-domain helpers shared by CLI command modules."""

import argparse
import re
import subprocess
import sys
import time
from typing import Any

from ...common.client import ProxmoxClient
from ...common.config import Config
from ...common.constants import (
    EXIT_API,
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_INTERNAL,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
)
from ...common.logging_ import Logger
from ...common.render import emit_error, emit_output


def _exit_code_for(resp: dict[str, Any]) -> int:
    """Map an API response to the CLI exit code."""
    if resp.get("ok"):
        return EXIT_SUCCESS
    status = resp.get("status_code")
    if status in {401, 403}:
        return EXIT_AUTH
    if status is None and "timed out" in str(resp.get("error", "")).lower():
        return EXIT_TIMEOUT
    return EXIT_API


def _extract_task(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("upid") or data.get("task")
    return None


def _require_config(config: Config, logger: Logger) -> int | None:
    if not config.ok:
        missing = config.missing()
        result = {
            "ok": False,
            "error_type": "config",
            "error": f"Missing required config: {', '.join(missing)}",
            "hint": f"Copy .env.example to .env and set the required values: {', '.join(missing)}",
            "log_path": logger.file_path,
        }
        logger.log(
            event="config_error",
            argv=sys.argv[1:],
            ok=False,
            error=result["error"],
        )
        emit_output(result, config.output_format)
        return EXIT_CONFIG
    return None


def _wait_for_task(
    node: str,
    upid: str,
    timeout: int,
    args: argparse.Namespace,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
) -> int:
    deadline = time.time() + timeout
    poll_interval = 2
    while time.time() < deadline:
        endpoint = f"/nodes/{node}/tasks/{upid}/status"
        resp = client.get(endpoint)
        logger.log(
            event="task_poll",
            argv=sys.argv[1:],
            method="GET",
            endpoint=endpoint,
            status_code=resp.get("status_code"),
            duration_ms=resp.get("duration_ms"),
            ok=resp["ok"],
            response_summary=f"node={node} upid={upid}",
            error=resp.get("error"),
            task_id=upid,
        )
        if not resp["ok"]:
            emit_output({"ok": False, "data": None, "error": resp.get("error")}, args.format, title="Task Wait")
            return _exit_code_for(resp)
        status_data = resp.get("data", {}) or {}
        if status_data.get("status") == "stopped":
            exitstatus = status_data.get("exitstatus", "unknown")
            ok = exitstatus == "OK"
            result = {"ok": ok, "data": status_data, "error": None if ok else f"Task failed: {exitstatus}"}
            emit_output(result, args.format, title="Task Wait")
            return _exit_code_for(result)
        time.sleep(poll_interval)
    emit_output({"ok": False, "data": None, "error": f"Timeout waiting for task {upid}"}, args.format, title="Task Wait")
    return EXIT_TIMEOUT

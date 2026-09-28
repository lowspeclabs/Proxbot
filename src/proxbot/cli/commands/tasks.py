"""Task polling, raw API passthrough, and log inspection handlers."""

import argparse
import json
import re
import subprocess
import sys
import time
from typing import Any

from ...common.config import Config
from ...common.constants import (
    EXIT_API,
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_INTERNAL,
    EXIT_NOOP,
    EXIT_SAFETY,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    EXIT_VALIDATION,
    REDACTED,
)
from ...common.logging_ import Logger, latest_log_file
from ...common.client import ProxmoxClient
from ...common.redact import redact_data, redact_string
from ...common.render import emit_error, emit_output, filter_rows, render_output
from ...common.safety import emit_dry_run, request_plan, require_destructive, require_execute

from ...common.constants import REPO_ROOT
from ._shared import _exit_code_for, _extract_task, _require_config, _wait_for_task


def cmd_tasks_recent(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/tasks"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} tasks={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "target": {"node": node}, "data": filter_rows(resp.get("data"), args), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Recent Tasks on {node}")
    return _exit_code_for(resp)


def cmd_task_status(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/tasks/{args.upid}/status"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} upid={args.upid}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title="Task Status")
    return _exit_code_for(resp)


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


def cmd_task_wait(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    upid = args.upid
    timeout = getattr(args, "wait_timeout", 120)
    return _wait_for_task(node, upid, timeout, args, config, client, logger)


def cmd_api_raw(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    method = args.method.lower()
    endpoint = args.endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    data: dict[str, Any] | None = None
    if args.data:
        try:
            parsed_data = json.loads(args.data)
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION
        if not isinstance(parsed_data, dict):
            emit_error("validation", "--data must be a JSON object", "", logger.file_path)
            return EXIT_VALIDATION
        data = parsed_data

    is_write = method in {"post", "put", "delete"}
    if is_write:
        plan = request_plan(method, endpoint, data, {"node": getattr(args, "node", None), "endpoint": endpoint})
        if method == "delete":
            guard = require_destructive(args, endpoint)
        else:
            guard = require_execute(args, endpoint)
        if guard is not None:
            if guard.get("ok"):
                return emit_dry_run(args, logger, plan)
            emit_output({"ok": False, "data": plan, "error": guard.get("error"), "hint": guard.get("hint")}, args.format, title="Safety Guard")
            return EXIT_SAFETY

    if method == "get":
        resp = client.get(endpoint)
    else:
        resp = getattr(client, method)(endpoint, data)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method=method.upper(),
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"raw_api method={method} endpoint={endpoint}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"API {method.upper()} {endpoint}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        node_match = re.search(r"/nodes/([^/]+)", endpoint)
        task_node = getattr(args, "node", None) or (node_match.group(1) if node_match else None)
        if not task_node:
            emit_error("validation", "--wait requires a node-scoped endpoint or --node", "Pass --node <node> so the task can be polled.", logger.file_path)
            return EXIT_VALIDATION
        return _wait_for_task(task_node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_logs_latest(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    latest = latest_log_file(config.log_dir)
    if latest is None:
        emit_error("not_found", "No log files found", "", None)
        return EXIT_NOOP
    print(str(latest))
    return EXIT_SUCCESS


def cmd_logs_path(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    print(str(config.log_dir))
    return EXIT_SUCCESS


def cmd_logs_tail(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    latest = latest_log_file(config.log_dir)
    if latest is None:
        emit_error("not_found", "No log files found", "", None)
        return EXIT_NOOP
    lines = getattr(args, "lines", 20)
    try:
        with latest.open("r", encoding="utf-8") as fh:
            all_lines = fh.readlines()
        for line in all_lines[-lines:]:
            print(line.rstrip("\n"))
    except OSError as exc:
        emit_error("io_error", str(exc), "", logger.file_path)
        return EXIT_API
    return EXIT_SUCCESS

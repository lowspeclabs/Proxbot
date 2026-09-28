"""Storage, network, and template registry handlers."""

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

from ._shared import _exit_code_for, _extract_task, _require_config, _wait_for_task


def cmd_storage_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/storage"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} storages={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Storage on {node}")
    return _exit_code_for(resp)


def cmd_storage_content(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/storage/{args.storage}/content"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} storage={args.storage}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Storage {args.storage} Content")
    return _exit_code_for(resp)


def cmd_network_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/network"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} networks={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Networks on {node}")
    return _exit_code_for(resp)


def cmd_storage_templates(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/storage/{args.storage}/content?content=vztmpl"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} storage={args.storage}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Templates on {args.storage}")
    return _exit_code_for(resp)


def cmd_storage_template_download(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/storage/{args.storage}/download-url"

    body = {
        "content": "vztmpl",
        "filename": args.filename,
        "url": args.url,
    }
    if getattr(args, "checksum", None):
        body["checksum"] = args.checksum
    if getattr(args, "data", None):
        try:
            extra = json.loads(args.data)
            body.update(extra)
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION

    guard = require_execute(args, endpoint)
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("POST", endpoint, body, {"node": node, "storage": args.storage}))

    resp = client.post(endpoint, body)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} storage={args.storage} filename={args.filename} task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title="Template Download")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, 120, args, config, client, logger)
    return _exit_code_for(resp)


def filter_registry_rows(
    data: Any,
    section: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> Any:
    """Filter upstream template registry entries client-side.

    The GET /nodes/{node}/aplinfo endpoint accepts no query parameters,
    so section/search/limit filtering happens here. Search matches
    substring (case-insensitive) against template, package, headline,
    description, os, and section fields.
    """
    if not isinstance(data, list):
        return data
    rows = data
    if section:
        rows = [r for r in rows if isinstance(r, dict) and str(r.get("section", "")).lower() == section.lower()]
    if search:
        needle = search.lower()
        def _matches(row: Any) -> bool:
            if not isinstance(row, dict):
                return False
            haystack = " ".join(str(row.get(k, "")) for k in ("template", "package", "headline", "description", "os", "section"))
            return needle in haystack.lower()
        rows = [r for r in rows if _matches(r)]
    if limit is not None:
        rows = rows[:limit]
    return rows


def cmd_templates_registry(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/aplinfo"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} registry_entries={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    data = filter_registry_rows(resp.get("data"), getattr(args, "section", None), getattr(args, "search", None), getattr(args, "limit", None))
    result = {"ok": resp["ok"], "data": data, "error": resp.get("error")}
    emit_output(result, args.format, title=f"Template Registry (node {node})")
    return _exit_code_for(resp)


def cmd_templates_download(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/aplinfo"
    body = {
        "storage": args.storage,
        "template": args.template,
    }

    guard = require_execute(args, endpoint)
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("POST", endpoint, body, {"node": node, "storage": args.storage}))

    resp = client.post(endpoint, body)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} storage={args.storage} template={args.template} task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title="Registry Template Download")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, 120, args, config, client, logger)
    return _exit_code_for(resp)

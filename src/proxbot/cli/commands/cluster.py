"""Cluster, node, health, and command-catalog handlers."""

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


def cmd_doctor(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    from .nodessh import probe_node_ssh

    data: dict[str, Any] = {
        ".env_path": str(config.env_path),
        ".env_found": config.env_path.exists(),
        "api_url": config.api_url or REDACTED,
        "token_id": config.token_id or REDACTED,
        "token_secret": REDACTED if config.token_secret else "missing",
        "ssl_verification": "enabled" if config.verify_ssl else "disabled (WARNING)",
        "log_directory": str(config.log_dir),
    }

    # Exec transports, reported whether or not the API is reachable. The
    # operator needs to know which of the three exec paths are available before
    # asking the agent to run anything.
    data["exec_transports"] = {
        "ssh_guest": "available when the guest has a resolvable IP and an installed key",
        "pct_exec": "available" if config.node_ssh_configured else "unavailable (not configured)",
        "console": "opt-in only (--console); shared pty, never used automatically",
    }
    if not config.node_ssh_configured:
        data["node_ssh"] = {
            "configured": False,
            "missing": config.node_ssh_missing(),
            "hint": "Set PROXMOX_NODE_SSH_USER and PROXMOX_NODE_SSH_KEY, then run `doctor --live`.",
        }

    if not config.ok:
        data["config_status"] = "missing required values: " + ", ".join(config.missing())
        result = {"ok": False, "error_type": "config", "data": data, "error": data["config_status"]}
        emit_output(result, args.format, title="Doctor")
        return EXIT_CONFIG

    if not args.live and not args.node_access:
        data["api_connectivity"] = "not checked (pass --live)"
        result = {"ok": True, "data": data, "error": None}
        emit_output(result, args.format, title="Doctor")
        return EXIT_SUCCESS

    start = time.time()
    resp = client.get("/version")
    duration_ms = int((time.time() - start) * 1000)
    data["api_connectivity"] = "ok" if resp["ok"] else "failed"
    version_data = resp.get("data") or {}
    summary = f"version={version_data.get('version', 'unknown')}"

    # Confirm node SSH / pct exec per node, and record which transports the
    # agent will actually have. This is the gate: pct exec stays unusable until
    # this probe succeeds, and the result is what the operator acts on.
    if args.node_access:
        probes = []
        requested_node = getattr(args, "node", None)
        if requested_node:
            probe_nodes = [requested_node]
        else:
            nodes_resp = client.get("/nodes")
            probe_nodes = [
                entry.get("node") for entry in (nodes_resp.get("data") or [])
                if isinstance(entry, dict) and entry.get("node")
            ] if nodes_resp.get("ok") else []
        for name in probe_nodes:
            probes.append(probe_node_ssh(name, config, client))
        data["node_access"] = probes
        data["pct_exec_available"] = any(p.get("usable") for p in probes)

    if not resp["ok"]:
        data["error"] = str(resp.get("error", "unknown"))
    logger.log(
        event="doctor",
        argv=sys.argv[1:],
        method="GET",
        endpoint="/version",
        status_code=resp.get("status_code"),
        duration_ms=duration_ms,
        ok=resp["ok"],
        response_summary=summary,
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": data, "error": resp.get("error")}
    emit_output(result, args.format, title="Doctor")
    return _exit_code_for(resp)


def cmd_health(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    endpoints = ["/version", "/cluster/status", "/nodes"]
    results: dict[str, Any] = {}
    ok = True
    for endpoint in endpoints:
        resp = client.get(endpoint)
        logger.log(
            event="api_call",
            argv=sys.argv[1:],
            method="GET",
            endpoint=endpoint,
            status_code=resp.get("status_code"),
            duration_ms=resp.get("duration_ms"),
            ok=resp["ok"],
            response_summary=f"data_keys={list((resp.get('data') or {}).keys()) if isinstance(resp.get('data'), dict) else 'list'}",
            error=resp.get("error"),
        )
        results[endpoint] = resp
        if not resp["ok"]:
            ok = False

    data: dict[str, Any] = {}
    if results.get("/version", {}).get("ok"):
        data["version"] = results["/version"]["data"]
    if results.get("/cluster/status", {}).get("ok"):
        data["cluster"] = results["/cluster/status"]["data"]
    if results.get("/nodes", {}).get("ok"):
        data["nodes"] = results["/nodes"]["data"]

    result = {"ok": ok, "data": data, "error": None if ok else "One or more health checks failed"}
    emit_output(result, args.format, title="Health")
    if ok:
        return EXIT_SUCCESS
    failed = next((r for r in results.values() if not r.get("ok")), None)
    return _exit_code_for(failed) if failed else EXIT_API


def cmd_commands(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    """Expose the supported canonical surface without requiring prose help parsing."""
    data = [
        {"command": "doctor", "write": False, "required": [], "note": "Pass --live to test API connectivity."},
        {"command": "health", "write": False, "required": []},
        {"command": "nodes list", "write": False, "required": []},
        {"command": "resolve --vmid <id> [--kind vm|lxc]", "write": False, "required": ["--vmid"], "note": "Find the node before a resource action."},
        {"command": "node status", "write": False, "required": ["--node"]},
        {"command": "vms list", "write": False, "required": ["--node"], "optional": ["--status", "--limit"]},
        {"command": "lxcs list", "write": False, "required": ["--node"], "optional": ["--status", "--limit"]},
        {"command": "vm <status|config|guest-network>", "write": False, "required": ["--node", "--vmid"]},
        {"command": "lxc <status|config>", "write": False, "required": ["--node", "--vmid"]},
        {"command": "storage <list|content|templates>", "write": False, "required": ["--node"]},
        {"command": "templates registry", "write": False, "required": ["--node"], "optional": ["--section", "--search", "--limit"], "note": "Browse upstream LXC template registry (pveam available)."},
        {"command": "templates download", "write": True, "required": ["--node", "--storage", "--template", "--execute"], "note": "Download a registry template by name (pveam download)."},
        {"command": "network list", "write": False, "required": ["--node"]},
        {"command": "tasks recent", "write": False, "required": ["--node"]},
        {"command": "vm exec", "write": True, "required": ["--node", "--vmid", "--command", "--execute"], "note": "Execute command inside VM via QEMU guest agent API."},
        {"command": "lxc exec", "write": True, "required": ["--node", "--vmid", "--command", "--execute"], "note": "Execute command directly inside LXC via Proxmox API without SSH."},
        {"command": "ssh exec", "write": True, "required": ["--command", "--execute", "--host OR --vmid"], "note": "Execute command over SSH on VM, LXC, or remote host."},
        {"command": "vm|lxc <start|shutdown|reboot|snapshot create>", "write": True, "required": ["--execute"]},
        {"command": "lxc create", "write": True, "required": ["--node", "--vmid", "--hostname", "--ostemplate", "--storage", "--password OR --password-fd", "--execute"], "optional": ["--ssh-public-keys OR --ssh-keygen"], "note": "Password required for a fresh OS tarball; SSH key is optional but strongly recommended. Use lxc clone for converted templates."},
        {"command": "vm clone", "write": True, "required": ["--node", "--vmid <template-vmid>", "--new-vmid <id>", "--execute"], "note": "Clone a VM or VM template (pct clone)."},
        {"command": "lxc clone", "write": True, "required": ["--node", "--vmid <template-vmid>", "--new-vmid <id>", "--execute"], "optional": ["--hostname", "--storage", "--pool", "--snapname"], "note": "Clone an LXC container or converted LXC template (template:1). Use this for template VMIDs; lxc create only accepts vztmpl tarballs."},
        {"command": "vm|lxc <stop|delete|snapshot delete>", "write": True, "required": ["--execute", "--force", "--confirm <vmid>"]},
        {"command": "api <get|post|put|delete> <endpoint>", "write": "method dependent", "required": ["--execute for POST/PUT", "--execute --force --confirm <endpoint> for DELETE"]},
    ]
    result = {
        "ok": True,
        "command": "commands",
        "target": None,
        "data": data,
        "dry_run": False,
        "task_id": None,
        "next_step": "Use nodes list, then list resources on the returned node.",
        "log_path": str(logger.file_path) if logger.file_path else None,
    }
    emit_output(result, args.format, title="Canonical Commands")
    return EXIT_SUCCESS


def cmd_nodes_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    resp = client.get("/nodes")
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint="/nodes",
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"nodes={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    data = resp.get("data", []) if resp["ok"] else None
    result = {"ok": resp["ok"], "data": data, "error": resp.get("error")}
    emit_output(result, args.format, title="Nodes")
    return _exit_code_for(resp)


def cmd_resolve(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    """Locate a VM or container by ID without guessing its node."""
    endpoint = "/cluster/resources"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"resolve vmid={args.vmid}",
        error=resp.get("error"),
    )
    if not resp["ok"]:
        emit_output({"ok": False, "target": {"vmid": args.vmid, "kind": args.kind}, "data": None, "error": resp.get("error")}, args.format, title="Resource Resolve")
        return _exit_code_for(resp)

    expected_types = {"vm": {"qemu"}, "lxc": {"lxc"}}
    matches = [
        row for row in resp.get("data", [])
        if isinstance(row, dict)
        and str(row.get("vmid")) == str(args.vmid)
        and (args.kind is None or row.get("type") in expected_types[args.kind])
    ]
    if not matches:
        emit_output({
            "ok": False,
            "error_type": "not_found",
            "target": {"vmid": args.vmid, "kind": args.kind},
            "data": [],
            "error": f"No matching resource found for VMID {args.vmid}.",
            "hint": "Run nodes list and then vms list or lxcs list on the returned node.",
        }, args.format, title="Resource Resolve")
        return EXIT_NOOP
    resource_name = "lxc" if matches[0].get("type") == "lxc" else "vm"
    next_step = f"Use {resource_name} status --node {matches[0].get('node')} --vmid {args.vmid}."
    emit_output({"ok": True, "target": {"vmid": args.vmid, "kind": args.kind}, "data": matches, "error": None, "next_step": next_step}, args.format, title="Resource Resolve")
    return EXIT_SUCCESS


def cmd_node_status(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/status"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"Node {node} Status")
    return _exit_code_for(resp)


def cmd_vms_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/qemu"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vms={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "target": {"node": node}, "data": filter_rows(resp.get("data"), args), "error": resp.get("error"), "next_step": "Use vm status --node <node> --vmid <vmid> for a selected VM."}
    emit_output(result, args.format, title=f"VMs on {node}")
    return _exit_code_for(resp)


def cmd_next_id(
    args: argparse.Namespace,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
    title: str = "Next Free ID",
) -> int:
    endpoint = "/cluster/nextid"
    if getattr(args, "vmid", None) is not None:
        endpoint = f"/cluster/nextid?vmid={args.vmid}"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"next_id={resp.get('data')}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": {"next_id": resp.get("data")}, "error": resp.get("error")}
    emit_output(result, args.format, title=title)
    return _exit_code_for(resp)

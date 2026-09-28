"""QEMU VM handlers: status, config, exec, lifecycle, snapshot, clone."""

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


def cmd_vm_status(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/qemu/{args.vmid}/status/current"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={args.vmid}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"VM {args.vmid} Status")
    return _exit_code_for(resp)


def cmd_vm_config(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/qemu/{args.vmid}/config"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={args.vmid}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"VM {args.vmid} Config")
    return _exit_code_for(resp)


def _diagnose_vm_agent(node: str, vmid: str | int, client: ProxmoxClient) -> dict[str, Any]:
    """Diagnose whether QEMU Guest Agent is enabled in Proxmox and responding in guest."""
    diagnosis: dict[str, Any] = {
        "configured_in_proxmox": False,
        "vm_status": "unknown",
        "agent_status": "unknown",
        "details": "",
        "hint": "",
        "remedy_command": None,
    }

    # 1. Check VM runtime status
    status_resp = client.get(f"/nodes/{node}/qemu/{vmid}/status/current")
    if status_resp.get("ok") and isinstance(status_resp.get("data"), dict):
        vm_status = status_resp["data"].get("status", "unknown")
        diagnosis["vm_status"] = vm_status
        if vm_status == "stopped":
            diagnosis["agent_status"] = "vm_stopped"
            diagnosis["details"] = f"VM {vmid} on node {node} is currently stopped."
            diagnosis["hint"] = f"Start the VM before interacting with guest agent: 'vm start --node {node} --vmid {vmid} --execute'."
            diagnosis["remedy_command"] = f"vm start --node {node} --vmid {vmid} --execute"
            return diagnosis

    # 2. Check VM configuration for agent option
    config_resp = client.get(f"/nodes/{node}/qemu/{vmid}/config")
    if config_resp.get("ok") and isinstance(config_resp.get("data"), dict):
        cfg = config_resp["data"]
        agent_cfg = cfg.get("agent")
        diagnosis["agent_config"] = agent_cfg

        # In Proxmox VE, agent setting can be '1', 'enabled=1', '1,fstrim_cloned_disks=1', etc.
        agent_enabled = False
        if agent_cfg is not None:
            agent_str = str(agent_cfg).strip()
            if agent_str in ("1", "true") or "enabled=1" in agent_str or (agent_str.isdigit() and int(agent_str) > 0):
                agent_enabled = True

        diagnosis["configured_in_proxmox"] = agent_enabled
        if not agent_enabled:
            diagnosis["agent_status"] = "not_configured_in_proxmox"
            diagnosis["details"] = f"QEMU Guest Agent is NOT enabled in Proxmox VM {vmid} configuration (agent option is disabled or not set)."
            diagnosis["hint"] = (
                f"Enable QEMU Guest Agent in Proxmox VM options (agent: 1) and restart the VM to attach the virtio-serial communication channel."
            )
            diagnosis["remedy_command"] = f"api post /nodes/{node}/qemu/{vmid}/config --data '{{\"agent\": \"1\"}}' --execute"
            return diagnosis
        else:
            diagnosis["agent_status"] = "not_running_in_guest"
            diagnosis["details"] = (
                f"QEMU Guest Agent is enabled in Proxmox VM options (agent: {agent_cfg}), but the daemon is not running or responding inside the guest OS."
            )
            diagnosis["hint"] = (
                f"Ensure 'qemu-guest-agent' is installed and active inside the guest OS ('apt install -y qemu-guest-agent && systemctl enable --now qemu-guest-agent'). "
                f"If the agent option was recently enabled in Proxmox, a full VM shutdown and restart is required to attach the virtio-serial device."
            )
            return diagnosis

    diagnosis["agent_status"] = "unreachable"
    diagnosis["details"] = f"Could not inspect VM {vmid} configuration or status on node {node}."
    diagnosis["hint"] = "Verify Proxmox node connectivity and API token permissions."
    return diagnosis


def cmd_vm_guest_network(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/qemu/{args.vmid}/agent/network-get-interfaces"
    resp = client.get(endpoint)
    logger.log(
        event="api_call", argv=sys.argv[1:], method="GET", endpoint=endpoint,
        status_code=resp.get("status_code"), duration_ms=resp.get("duration_ms"), ok=resp["ok"],
        response_summary=f"node={node} vmid={args.vmid} guest_network", error=resp.get("error"),
    )
    if not resp["ok"]:
        diagnosis = _diagnose_vm_agent(node, str(args.vmid), client)
        result = {
            "ok": False,
            "target": {"node": node, "vmid": args.vmid},
            "error": resp.get("error") or f"Guest agent network query failed on VM {args.vmid}",
            "agent_diagnosis": diagnosis,
            "hint": diagnosis.get("hint") or "Install and start qemu-guest-agent in the guest.",
            "next_step": "Fix the guest agent configuration or use SSH directly if guest IP is known.",
        }
        emit_output(result, args.format, title=f"VM {args.vmid} Guest Network")
        return _exit_code_for(resp)

    result = {
        "ok": True,
        "target": {"node": node, "vmid": args.vmid},
        "data": resp.get("data"),
        "error": None,
        "hint": None,
        "next_step": "Use the returned guest IP with SSH instead of VNC when SSH is configured.",
    }
    emit_output(result, args.format, title=f"VM {args.vmid} Guest Network")
    return EXIT_SUCCESS


def cmd_vm_exec(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    command = (getattr(args, "command", None) or "").strip()
    timeout = getattr(args, "timeout", None) or 30

    if not command:
        emit_error("validation", "--command is required", "Provide a non-empty shell command to execute.", logger.file_path)
        return EXIT_VALIDATION

    endpoint = f"/nodes/{node}/qemu/{vmid}/agent/exec"
    guard = require_execute(args, f"vm_exec:{node}:{vmid}")
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("POST", endpoint, {"command": command}, {"node": node, "vmid": vmid, "command": command}))

    # Initiate execution via QEMU guest agent
    post_resp = client.post(endpoint, {"command": ["/bin/sh", "-c", command]})
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST",
        endpoint=endpoint,
        status_code=post_resp.get("status_code"),
        duration_ms=post_resp.get("duration_ms"),
        ok=post_resp["ok"],
        response_summary=f"node={node} vmid={vmid} vm_exec initiate",
        error=post_resp.get("error"),
    )

    if not post_resp["ok"]:
        diagnosis = _diagnose_vm_agent(node, vmid, client)
        error_msg = post_resp.get("error") or "Failed to initiate command in VM via QEMU guest agent"
        emit_output({
            "ok": False,
            "target": {"node": node, "vmid": vmid},
            "error": error_msg,
            "agent_diagnosis": diagnosis,
            "hint": diagnosis.get("hint") or "Verify VM state and guest agent options (agent: 1) in Proxmox.",
        }, args.format, title=f"VM {vmid} Exec Error")
        return _exit_code_for(post_resp)

    pid_val = post_resp.get("data")
    pid = pid_val.get("pid") if isinstance(pid_val, dict) else pid_val
    if not pid:
        emit_error("api_error", "No PID returned from agent/exec endpoint", "Check Proxmox guest agent response.", logger.file_path)
        return EXIT_API

    # Poll status endpoint until process exits or timeout expires
    status_endpoint = f"/nodes/{node}/qemu/{vmid}/agent/exec-status?pid={pid}"
    start_time = time.time()
    exited = False
    status_data = {}

    while time.time() - start_time < timeout:
        status_resp = client.get(status_endpoint)
        if not status_resp["ok"]:
            time.sleep(0.5)
            continue
        status_data = status_resp.get("data", {})
        if status_data.get("exited") == 1 or status_data.get("exited") is True:
            exited = True
            break
        time.sleep(0.5)

    duration_ms = int((time.time() - start_time) * 1000)

    if not exited:
        logger.log(
            event="api_call",
            argv=sys.argv[1:],
            method="GET",
            endpoint=status_endpoint,
            status_code=200,
            duration_ms=duration_ms,
            ok=False,
            response_summary=f"node={node} vmid={vmid} vm_exec timed out after {timeout}s",
            error=f"Command timed out after {timeout}s",
        )
        emit_output({
            "ok": False,
            "target": {"node": node, "vmid": vmid},
            "error": f"Command timed out after {timeout}s (PID {pid} still running)",
            "hint": "Increase timeout with --timeout <seconds> or check guest process status.",
            "data": {"pid": pid, "node": node, "vmid": int(vmid), "command": command},
        }, args.format, title=f"VM {vmid} Exec Timeout")
        return EXIT_API

    exitcode = status_data.get("exitcode", 0)
    stdout = status_data.get("out-data", "")
    stderr = status_data.get("err-data", "")
    ok = (exitcode == 0)

    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST",
        endpoint=f"/nodes/{node}/qemu/{vmid}/agent/exec",
        status_code=200,
        duration_ms=duration_ms,
        ok=ok,
        response_summary=f"node={node} vmid={vmid} vm_exec pid={pid} exitcode={exitcode}",
        error=stderr if not ok else None,
    )

    result_data = {
        "node": node,
        "vmid": int(vmid),
        "command": command,
        "exit_code": exitcode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "pid": pid,
    }

    result = {
        "ok": ok,
        "target": {"node": node, "vmid": vmid},
        "data": result_data,
        "error": stderr.strip() if not ok and stderr else (f"Command exited with code {exitcode}" if not ok else None),
    }
    emit_output(result, args.format, title=f"VM {vmid} Exec Output")
    return EXIT_SUCCESS if ok else EXIT_API


def _vm_state_change(
    args: argparse.Namespace,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
    action: str,
    destructive: bool = False,
) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/qemu/{vmid}/status/{action}"

    if destructive:
        guard = require_destructive(args, vmid)
    else:
        guard = require_execute(args, endpoint)
    if guard is not None:
        if guard.get("ok"):
            return emit_dry_run(args, logger, request_plan("POST", endpoint, target={"node": node, "vmid": vmid, "action": action}))
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            method="POST",
            endpoint=endpoint,
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    resp = client.post(endpoint)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid} action={action} task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"VM {vmid} {action.title()}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_vm_start(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_state_change(args, config, client, logger, "start")


def cmd_vm_shutdown(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_state_change(args, config, client, logger, "shutdown")


def cmd_vm_stop(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_state_change(args, config, client, logger, "stop", destructive=True)


def cmd_vm_reboot(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_state_change(args, config, client, logger, "reboot")


def cmd_vm_create(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/qemu"

    body: dict[str, Any] = {
        "vmid": vmid,
        "name": args.name,
    }
    if getattr(args, "storage", None):
        body["storage"] = args.storage
    if getattr(args, "memory", None) is not None:
        body["memory"] = args.memory
    if getattr(args, "cores", None) is not None:
        body["cores"] = args.cores
    if getattr(args, "sockets", None) is not None:
        body["sockets"] = args.sockets
    if getattr(args, "cpu", None):
        body["cpu"] = args.cpu
    if getattr(args, "ostype", None):
        body["ostype"] = args.ostype
    if getattr(args, "net0", None):
        body["net0"] = args.net0
    if getattr(args, "scsihw", None):
        body["scsihw"] = args.scsihw
    if getattr(args, "onboot", None) is not None:
        body["onboot"] = 1 if args.onboot else 0
    if getattr(args, "data", None):
        try:
            extra = json.loads(args.data)
            body.update(extra)
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION

    guard = require_execute(args, endpoint)
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("POST", endpoint, body, {"node": node, "vmid": vmid}))

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
        response_summary=f"node={node} vmid={vmid} action=create task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"VM {vmid} Create")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_vm_delete(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/qemu/{vmid}"

    guard = require_destructive(args, vmid)
    if guard is not None:
        if guard.get("ok"):
            return emit_dry_run(args, logger, request_plan("DELETE", endpoint, target={"node": node, "vmid": vmid, "action": "delete"}))
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            method="DELETE",
            endpoint=endpoint,
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    resp = client.delete(endpoint)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="DELETE",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid} action=delete task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"VM {vmid} Delete")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_vm_snapshot_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/qemu/{args.vmid}/snapshot"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={args.vmid}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"VM {args.vmid} Snapshots")
    return _exit_code_for(resp)


def _vm_snapshot_change(
    args: argparse.Namespace,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
    action: str,
    destructive: bool = False,
) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    name = args.name
    if action == "create":
        endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot"
    else:
        endpoint = f"/nodes/{node}/qemu/{vmid}/snapshot/{name}"

    if destructive:
        guard = require_destructive(args, vmid)
    else:
        guard = require_execute(args, endpoint)
    if guard is not None:
        if guard.get("ok"):
            return emit_dry_run(args, logger, request_plan("POST" if action == "create" else "DELETE", endpoint, {"snapname": name} if action == "create" else None, {"node": node, "vmid": vmid, "snapshot": name, "action": action}))
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            method="POST" if action == "create" else "DELETE",
            endpoint=endpoint,
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    if action == "create":
        body: dict[str, Any] | None = {"snapname": name}
        resp = client.post(endpoint, body)
    else:
        resp = client.delete(endpoint)
    task_id = _extract_task(resp.get("data"))
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="POST" if action == "create" else "DELETE",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid} snapshot={name} action={action} task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"VM {vmid} Snapshot {action.title()}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_vm_snapshot_create(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_snapshot_change(args, config, client, logger, "create")


def cmd_vm_snapshot_delete(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _vm_snapshot_change(args, config, client, logger, "delete", destructive=True)


def _clone_execute(
    args: argparse.Namespace,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
    kind: str,
) -> int:
    """Shared clone implementation for LXC (`pct clone`) and QEMU templates."""
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    source_vmid = str(args.vmid)
    new_vmid = str(getattr(args, "new_vmid", None) or "")
    if not new_vmid:
        emit_error("validation", "--new-vmid is required", "Use `lxcs next-id` / `vms next-id` to find a free ID.", logger.file_path)
        return EXIT_VALIDATION
    if kind == "lxc":
        endpoint = f"/nodes/{node}/lxc/{source_vmid}/clone"
    else:
        endpoint = f"/nodes/{node}/qemu/{source_vmid}/clone"
    body: dict[str, Any] = {"newid": int(new_vmid)}
    if kind == "lxc":
        if getattr(args, "hostname", None):
            body["hostname"] = args.hostname
    else:
        if getattr(args, "name", None):
            body["name"] = args.name
    if getattr(args, "storage", None):
        body["storage"] = args.storage
    if getattr(args, "pool", None):
        body["pool"] = args.pool
    if getattr(args, "full", None) is not None:
        body["full"] = 1 if args.full else 0
    if getattr(args, "snapname", None):
        body["snapname"] = args.snapname
    if getattr(args, "data", None):
        try:
            extra = json.loads(args.data)
            body.update(extra)
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION

    guard = require_execute(args, endpoint)
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("POST", endpoint, body, {"node": node, "vmid": source_vmid, "new_vmid": new_vmid}))

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
        response_summary=f"node={node} vmid={source_vmid} new_vmid={new_vmid} action=clone task={task_id or 'n/a'}",
        error=resp.get("error"),
        task_id=task_id,
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error"), "task_id": task_id}
    emit_output(result, args.format, title=f"{kind.upper()} {source_vmid} Clone -> {new_vmid}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_vm_clone(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _clone_execute(args, config, client, logger, "qemu")

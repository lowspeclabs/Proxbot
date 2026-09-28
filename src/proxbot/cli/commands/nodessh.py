"""Node-side execution for LXC via `pct exec` over SSH.

The Proxmox REST API has no exec endpoint for LXC (its subdir index is clone,
config, firewall, interfaces, migrate, pending, resize, rrd, rrddata, snapshot,
spiceproxy, status, termproxy, vncproxy, vncwebsocket). `pct exec` is a node-side
CLI, so reaching it means SSH to the node.

This module deliberately does not expose a general node shell. The command is
assembled here, in one place, as:

    pct exec <vmid> -- /bin/sh -lc <command>

and the caller supplies only ``vmid`` and ``command``. Nothing else is
interpolated into the remote command line.
"""

from __future__ import annotations

import argparse
import ipaddress
import shlex
import subprocess
import sys
import time
from typing import Any

from ...common.client import ProxmoxClient
from ...common.config import Config
from ...common.constants import EXIT_INTERNAL, EXIT_SUCCESS, EXIT_TIMEOUT, EXIT_VALIDATION
from ...common.logging_ import Logger
from ...common.render import emit_error, emit_output
from ...common.safety import emit_dry_run, request_plan, require_execute

# Cheap, side-effect-free command used to confirm node SSH actually works.
# Deliberately not `id` or `hostname` alone: we want to confirm both that SSH
# authenticates and that the remote end is actually a Proxmox node.
_PROBE_COMMAND = "pct version"


def assemble_pct_exec(vmid: str, command: str) -> str:
    """Build the remote command line. The only place a pct command is composed."""
    return f"pct exec {shlex.quote(str(vmid))} -- /bin/sh -lc {shlex.quote(command)}"


def _node_addresses(client: ProxmoxClient | None) -> dict[str, str]:
    """Map node name -> best IPv4 address, from the cluster's own view."""
    if client is None:
        return {}
    resp = client.get("/nodes")
    if not resp.get("ok") or not isinstance(resp.get("data"), list):
        return {}

    addresses: dict[str, str] = {}
    for entry in resp["data"]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("node")
        if not name:
            continue
        node_resp = client.get(f"/nodes/{name}/network")
        if not node_resp.get("ok") or not isinstance(node_resp.get("data"), list):
            continue
        best: str | None = None
        for iface in node_resp["data"]:
            if not isinstance(iface, dict):
                continue
            cidr = iface.get("cidr")
            if not cidr:
                continue
            try:
                addr = ipaddress.ip_interface(str(cidr)).ip
            except ValueError:
                continue
            if addr.version != 4 or addr.is_loopback:
                continue
            # Prefer an address on a bridge: that is the management network the
            # guests and the API live on.
            if best is None or iface.get("type") == "bridge":
                best = str(addr)
        if best:
            addresses[name] = best
    return addresses


def probe_node_ssh(node: str, config: Config, client: ProxmoxClient | None) -> dict[str, Any]:
    """Confirm whether `pct exec` is usable for a node. Read-only; runs `pct version`."""
    report: dict[str, Any] = {
        "node": node,
        "configured": config.node_ssh_configured,
        "usable": False,
        "missing": config.node_ssh_missing(),
    }
    if not config.node_ssh_configured:
        report["reason"] = (
            "Node SSH is not configured. Set PROXMOX_NODE_SSH_USER and "
            "PROXMOX_NODE_SSH_KEY to enable the pct exec transport. Until then "
            "the only exec paths are guest SSH and the opt-in console."
        )
        return report

    address = _node_addresses(client).get(node)
    if not address:
        report["reason"] = (
            f"No IPv4 address found for node {node}. The cluster did not return a "
            f"usable address for it, so there is nowhere to SSH to."
        )
        return report

    report["address"] = address
    report["user"] = config.node_ssh_user
    report["key_file"] = config.node_ssh_key
    report["auth_method"] = config.node_ssh_auth_method

    from .exec import _run_ssh_command  # local import: avoids an import cycle

    start = time.time()
    try:
        code, stdout, stderr = _run_ssh_command(
            host=address,
            port=config.node_ssh_port,
            user=config.node_ssh_user,
            command=_PROBE_COMMAND,
            key_file=config.node_ssh_key,
            password=config.node_ssh_password,
            timeout=config.node_ssh_timeout,
        )
    except Exception as exc:  # ssh binary missing, DNS failure, etc.
        report["reason"] = f"SSH to {config.node_ssh_user}@{address} failed: {type(exc).__name__}: {exc}"
        return report

    report["duration_ms"] = int((time.time() - start) * 1000)
    report["exit_code"] = code
    if code != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        report["reason"] = (
            f"`{_PROBE_COMMAND}` on {config.node_ssh_user}@{address} exited {code}: "
            + (detail[-1] if detail else "no output")
        )
        return report

    report["usable"] = True
    report["pct_version"] = (stdout or "").strip().splitlines()[:2]
    report["reason"] = (
        f"Node SSH works and pct is present, so `pct exec` is available for containers on {node}."
    )
    return report


def cmd_pct_exec(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    """Run a command inside an LXC via the node. Node, argv and user are not caller-supplied."""
    node = args.node or config.default_node
    if not node:
        emit_error(
            "validation",
            "--node is required",
            "Set PROXMOX_DEFAULT_NODE or pass --node. The node is resolved by the harness, "
            "not taken from the command.",
            logger.file_path,
        )
        return EXIT_VALIDATION

    vmid = str(args.vmid)
    command = (getattr(args, "command", None) or "").strip()
    if not command:
        emit_error("validation", "--command is required", "Provide a non-empty shell command.", logger.file_path)
        return EXIT_VALIDATION
    if not str(vmid).isdigit():
        emit_error(
            "validation",
            "--vmid must be numeric",
            f"Refusing non-numeric vmid {vmid!r}; it is interpolated into the node command line.",
            logger.file_path,
        )
        return EXIT_VALIDATION

    remote = assemble_pct_exec(vmid, command)

    guard = require_execute(args, f"pct_exec:{node}:{vmid}")
    if guard is not None:
        return emit_dry_run(
            args,
            logger,
            request_plan(
                "PCT EXEC",
                f"ssh://{config.node_ssh_user or 'unset'}@{node}:{config.node_ssh_port}",
                {"vmid": vmid, "command": command},
                {"node": node, "vmid": vmid, "command": command, "remote": remote},
            ),
        )

    if not config.node_ssh_configured:
        emit_output(
            {
                "ok": False,
                "status": "node_ssh_unconfigured",
                "data": {"node": node, "vmid": vmid, "missing": config.node_ssh_missing()},
                "error": (
                    f"pct exec is unavailable: node SSH is not configured (missing "
                    f"{', '.join(config.node_ssh_missing())}). Run `doctor --live --node-access` to "
                    f"re-check, or use guest SSH. No host-side command was attempted."
                ),
            },
            args.format,
            title=f"PCT Exec ({vmid} on {node})",
        )
        return EXIT_VALIDATION

    from .exec import _run_ssh_command

    address = _node_addresses(client).get(node)
    if not address:
        emit_output(
            {
                "ok": False,
                "status": "node_unreachable",
                "data": {"node": node, "vmid": vmid},
                "error": f"No IPv4 address known for node {node}; cannot SSH to it.",
            },
            args.format,
            title=f"PCT Exec ({vmid} on {node})",
        )
        return EXIT_INTERNAL

    timeout = getattr(args, "timeout", None) or config.timeout_seconds
    # An explicit --node-password overrides the configured one for this call.
    node_password = getattr(args, "node_password", None) or config.node_ssh_password
    start = time.time()
    try:
        code, stdout, stderr = _run_ssh_command(
            host=address,
            port=config.node_ssh_port,
            user=config.node_ssh_user,
            command=remote,
            key_file=config.node_ssh_key,
            password=node_password,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        emit_output(
            {
                "ok": False,
                "status": "timeout",
                "data": {"node": node, "vmid": vmid, "command": command},
                "error": f"pct exec timed out after {timeout}s on {node}.",
            },
            args.format,
            title=f"PCT Exec ({vmid} on {node})",
        )
        return EXIT_TIMEOUT
    except FileNotFoundError:
        emit_error("internal", "ssh binary not found on local PATH", "Install openssh-client.", logger.file_path)
        return EXIT_INTERNAL
    except Exception as exc:
        emit_output(
            {
                "ok": False,
                "status": "node_ssh_failed",
                "data": {"node": node, "vmid": vmid},
                "error": f"Node SSH failed: {type(exc).__name__}: {exc}",
            },
            args.format,
            title=f"PCT Exec ({vmid} on {node})",
        )
        return EXIT_INTERNAL

    duration_ms = int((time.time() - start) * 1000)
    result = {
        "ok": code == 0,
        "data": {
            "node": node,
            "vmid": vmid,
            "address": address,
            "transport": "pct_exec",
            "exit_code": code,
            "stdout": stdout,
            "stderr": stderr,
            "command": command,
            "duration_ms": duration_ms,
        },
        "error": (stderr or None) if code != 0 else None,
    }
    logger.log(
        event="pct_exec",
        argv=sys.argv[1:],
        ok=result["ok"],
        response_summary=f"node={node} vmid={vmid} exit_code={code} duration_ms={duration_ms}",
        extra={"exit_code": code, "duration_ms": duration_ms, "node": node},
    )
    emit_output(result, args.format, title=f"PCT Exec ({vmid} on {node})")
    return EXIT_SUCCESS if code == 0 else EXIT_INTERNAL

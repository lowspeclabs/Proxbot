"""LXC container handlers: status, config, create, clone, exec, snapshot, ssh keys."""

import argparse
import json
import os
import pathlib
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
from ...common.render import emit_error, emit_output, filter_rows, render_markdown, render_output
from ...common.safety import emit_dry_run, request_plan, require_destructive, require_execute

from ...common.constants import REPO_ROOT
from ._shared import _exit_code_for, _extract_task, _require_config, _wait_for_task


def cmd_lxcs_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/lxc"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="GET",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} lxcs={len(resp.get('data', [])) if isinstance(resp.get('data'), list) else 'n/a'}",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "target": {"node": node}, "data": filter_rows(resp.get("data"), args), "error": resp.get("error"), "next_step": "Use lxc status --node <node> --vmid <vmid> for a selected container."}
    emit_output(result, args.format, title=f"LXCs on {node}")
    return _exit_code_for(resp)


def cmd_lxc_status(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/lxc/{args.vmid}/status/current"
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
    emit_output(result, args.format, title=f"LXC {args.vmid} Status")
    return _exit_code_for(resp)


def cmd_lxc_config(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/lxc/{args.vmid}/config"
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
    emit_output(result, args.format, title=f"LXC {args.vmid} Config")
    return _exit_code_for(resp)


def cmd_lxc_set_config(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    """Update an LXC container's config (PUT), e.g. static IP via --net0."""
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/lxc/{vmid}/config"
    body: dict[str, Any] = {}
    for field in ("net0", "hostname", "nameserver", "searchdomain"):
        value = getattr(args, field, None)
        if value is not None:
            body[field] = value
    if getattr(args, "data", None):
        try:
            body.update(json.loads(args.data))
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION
    if not body:
        emit_error(
            "validation",
            "Nothing to update: pass at least one of --net0, --hostname, --nameserver, --searchdomain, or --data.",
            "Read the current config with `lxc config` first; lxc config itself is read-only.",
            logger.file_path,
        )
        return EXIT_VALIDATION

    guard = require_execute(args, endpoint)
    if guard is not None:
        if guard.get("ok"):
            return emit_dry_run(args, logger, request_plan("PUT", endpoint, body, {"node": node, "vmid": vmid}))
        logger.log(
            event="safety_guard",
            argv=sys.argv[1:],
            method="PUT",
            endpoint=endpoint,
            ok=guard.get("ok", True),
            dry_run=True,
            response_summary=guard.get("message", "blocked by safety guard"),
        )
        emit_output({"ok": guard.get("ok", True), "data": guard, "error": guard.get("error")}, args.format, title="Dry Run")
        return EXIT_SAFETY if guard.get("ok") is False else EXIT_SUCCESS

    resp = client.put(endpoint, body)
    logger.log(
        event="api_call",
        argv=sys.argv[1:],
        method="PUT",
        endpoint=endpoint,
        status_code=resp.get("status_code"),
        duration_ms=resp.get("duration_ms"),
        ok=resp["ok"],
        response_summary=f"node={node} vmid={vmid} action=set-config",
        error=resp.get("error"),
    )
    result = {"ok": resp["ok"], "data": resp.get("data"), "error": resp.get("error")}
    emit_output(result, args.format, title=f"LXC {vmid} Config Updated")
    return _exit_code_for(resp)


def _lxc_state_change(
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
    endpoint = f"/nodes/{node}/lxc/{vmid}/status/{action}"

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
    emit_output(result, args.format, title=f"LXC {vmid} {action.title()}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_lxc_start(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _lxc_state_change(args, config, client, logger, "start")


def cmd_lxc_shutdown(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _lxc_state_change(args, config, client, logger, "shutdown")


def cmd_lxc_stop(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _lxc_state_change(args, config, client, logger, "stop", destructive=True)


def _is_debian_13_template(ostemplate: str | None) -> bool:
    if not ostemplate:
        return False
    return "debian-13" in ostemplate.lower()


OSTEMPLATE_RE = re.compile(r"^[^:]+:vztmpl/.+\.tar\.(zst|xz|gz)$")


def _validate_ostemplate(ostemplate: str | None) -> str | None:
    """Return an error hint if ostemplate is not a valid vztmpl volid, else None."""
    if not ostemplate:
        return "Missing --ostemplate. Expected format: <storage>:vztmpl/<file>.tar.zst"
    if OSTEMPLATE_RE.match(ostemplate):
        return None
    if re.match(r"^\d+$", ostemplate.strip()):
        return (
            f"Invalid --ostemplate '{ostemplate}': a numeric VMID is a converted "
            "LXC template (template:1), not a vztmpl tarball. "
            "Use `lxc clone --node <node> --vmid <template-vmid> --new-vmid <id> "
            "--hostname <name> --storage <pool>` instead of `lxc create`."
        )
    return (
        f"Invalid --ostemplate '{ostemplate}'. Expected format: "
        "<storage>:vztmpl/<file>.tar.zst (e.g. local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst). "
        "Converted LXC templates (template:1 VMIDs) must use `lxc clone`, not `lxc create`."
    )


def _lxc_console_fix_instructions(vmid: str | int) -> str:
    return f"""\
Note: Debian 13 LXC containers may show a blank Proxmox web console because
systemd 257's console-getty.service and container-getty@.service include
ImportCredential directives that fail inside LXC (status=243/CREDENTIALS).
No login prompt appears on /dev/console or /dev/tty1.

To fix VMID {vmid}, run these commands on the Proxmox node:

  pct exec {vmid} -- mkdir -p /etc/systemd/system/console-getty.service.d
  pct exec {vmid} -- sh -c 'printf "[Service]\\nImportCredential=\\n" > /etc/systemd/system/console-getty.service.d/override.conf'
  pct exec {vmid} -- mkdir -p /etc/systemd/system/container-getty@.service.d
  pct exec {vmid} -- sh -c 'printf "[Service]\\nImportCredential=\\n" > /etc/systemd/system/container-getty@.service.d/override.conf'
  pct exec {vmid} -- systemctl daemon-reload
  pct exec {vmid} -- systemctl reset-failed console-getty.service container-getty@1.service container-getty@2.service
  pct exec {vmid} -- systemctl start console-getty.service container-getty@1.service container-getty@2.service
"""


def cmd_lxc_create(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/lxc"

    password = getattr(args, "password", None)
    password_fd = getattr(args, "password_fd", None)
    if password_fd is not None:
        try:
            with os.fdopen(os.dup(password_fd), "r", encoding="utf-8") as password_pipe:
                password = password_pipe.readline(4097).rstrip("\r\n")
        except (OSError, ValueError) as exc:
            emit_error("validation", f"Could not read --password-fd: {exc}", "Pass the password through an inherited readable file descriptor.", logger.file_path)
            return EXIT_VALIDATION
    if not password:
        emit_error(
            "validation",
            "A root password is required when creating an LXC from an OS tarball.",
            "Supply --password (visible in the process table) or use --password-fd. SSH public-key injection is optional but strongly recommended.",
            logger.file_path,
        )
        return EXIT_VALIDATION

    ostemplate_err = _validate_ostemplate(getattr(args, "ostemplate", None))
    if ostemplate_err:
        emit_error("validation", ostemplate_err, "Run `storage templates --node <node> --storage local` for valid vztmpl volids.", logger.file_path)
        return EXIT_VALIDATION

    ssh_public_keys = getattr(args, "ssh_public_keys", None)
    ssh_keygen_flag = getattr(args, "ssh_keygen", None)

    features_val = getattr(args, "features", None)
    body: dict[str, Any] = {
        "vmid": vmid,
        "hostname": args.hostname,
        "ostemplate": args.ostemplate,
        "storage": args.storage,
    }
    if features_val is not None:
        body["features"] = features_val
    if getattr(args, "unprivileged", None) is not None:
        body["unprivileged"] = 1 if args.unprivileged else 0
    body["password"] = password
    if getattr(args, "cores", None) is not None:
        body["cores"] = args.cores
    if getattr(args, "memory", None) is not None:
        body["memory"] = args.memory
    if getattr(args, "swap", None) is not None:
        body["swap"] = args.swap
    if getattr(args, "net0", None):
        body["net0"] = args.net0
    if getattr(args, "rootfs", None):
        body["rootfs"] = args.rootfs
    if getattr(args, "nameserver", None):
        body["nameserver"] = args.nameserver
    if getattr(args, "onboot", None) is not None:
        body["onboot"] = 1 if args.onboot else 0

    if ssh_public_keys:
        body["ssh-public-keys"] = ssh_public_keys
    elif ssh_keygen_flag:
        body["ssh-public-keys"] = "<generated on execute>"

    if getattr(args, "data", None):
        try:
            extra = json.loads(args.data)
            body.update(extra)
        except json.JSONDecodeError:
            emit_error("validation", "--data must be valid JSON", "", logger.file_path)
            return EXIT_VALIDATION

    guard = require_execute(args, endpoint)
    if guard is not None:
        plan_target: dict[str, Any] = {"node": node, "vmid": vmid}
        if getattr(args, "as_template", False):
            plan_target["then"] = f"POST /nodes/{node}/lxc/{vmid}/template"
        return emit_dry_run(args, logger, request_plan("POST", endpoint, body, plan_target))

    if ssh_keygen_flag:
        keygen_path = getattr(args, "ssh_key_path", None) or str(REPO_ROOT / f".lxc-ssh-key-{vmid}")
        keygen_comment = f"lxc-{vmid}-{args.hostname}-opencode"
        keypath = pathlib.Path(keygen_path)
        pubpath = keypath.with_suffix(".pub")
        if keypath.exists() and pubpath.exists():
            body["ssh-public-keys"] = pubpath.read_text().strip()
        elif keypath.exists():
            emit_error("validation", f"SSH key already exists at {keypath} without a .pub file. Remove it or use --ssh-public-keys.", "", logger.file_path)
            return EXIT_VALIDATION
        else:
            keypath.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run(
                    ["ssh-keygen", "-t", "ed25519", "-C", keygen_comment, "-f", str(keypath), "-N", ""],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    emit_error("validation", f"ssh-keygen failed: {result.stderr}", "", logger.file_path)
                    return EXIT_INTERNAL
            except FileNotFoundError:
                emit_error("validation", "ssh-keygen not found on this system", "", logger.file_path)
                return EXIT_INTERNAL
            body["ssh-public-keys"] = keypath.with_suffix(".pub").read_text().strip()

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
    emit_output(result, args.format, title=f"LXC {vmid} Create")

    if not resp["ok"]:
        return _exit_code_for(resp)

    exit_code = EXIT_SUCCESS
    if task_id and getattr(args, "wait", False):
        exit_code = _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
        if exit_code != EXIT_SUCCESS:
            return exit_code

    # Convert to a template. This is how a keyed base image is built: create with
    # --ssh-public-keys/--ssh-keygen, then mark it so every future clone inherits
    # the key. `lxc clone` has no --ssh-public-keys, so baking the key in here is
    # the only way clones come up reachable.
    if getattr(args, "as_template", False):
        tpl_endpoint = f"/nodes/{node}/lxc/{vmid}/template"
        tpl_resp = client.post(tpl_endpoint, {})
        logger.log(
            event="api_call",
            argv=sys.argv[1:],
            method="POST",
            endpoint=tpl_endpoint,
            status_code=tpl_resp.get("status_code"),
            duration_ms=tpl_resp.get("duration_ms"),
            ok=tpl_resp["ok"],
            response_summary=f"node={node} vmid={vmid} action=template",
            error=tpl_resp.get("error"),
        )
        emit_output(
            {"ok": tpl_resp["ok"], "data": tpl_resp.get("data"), "error": tpl_resp.get("error")},
            args.format,
            title=f"LXC {vmid} Convert To Template",
        )
        if not tpl_resp["ok"]:
            return _exit_code_for(tpl_resp)
        print()
        print(f"LXC {vmid} is now a template. Clone from it with:")
        print(f"  lxc clone --node {node} --vmid {vmid} --new-vmid <id> --hostname <name> --execute")
        print()
        print("Clones of it inherit the injected SSH key, so guest SSH works without a")
        print("console login. If you also want a working console on clones, apply the")
        print("Debian 13 console drop-in to this template first: start it temporarily,")
        print("run `lxc pct-exec`, then stop and convert.")
        return EXIT_SUCCESS

    if _is_debian_13_template(args.ostemplate):
        print()
        print(_lxc_console_fix_instructions(vmid))
    return exit_code


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


def cmd_lxc_clone(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _clone_execute(args, config, client, logger, "lxc")


def cmd_vm_clone(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _clone_execute(args, config, client, logger, "qemu")


def cmd_lxc_ssh_keygen(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    keyfile = args.keyfile or str(REPO_ROOT / ".lxc-ssh-key")
    vmid_part = args.vmid if getattr(args, "vmid", None) else "default"
    comment = args.comment or f"lxc-{vmid_part}-opencode"
    keypath = pathlib.Path(keyfile)

    guard = require_execute(args, f"local:ssh-keygen:{keypath}")
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("LOCAL", f"ssh-keygen:{keypath}", {"comment": comment}, {"keyfile": str(keypath)}))

    if keypath.exists() and not args.force:
        emit_output({
            "ok": False,
            "data": {"keyfile": str(keypath)},
            "error": f"Key file already exists at {keypath}. Use --force to overwrite."
        }, args.format, title="SSH Key Generation")
        return EXIT_NOOP

    if keypath.exists():
        keypath.unlink()
        pubkey_path = keypath.with_suffix(".pub")
        if pubkey_path.exists():
            pubkey_path.unlink()

    keypath.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-C", comment, "-f", str(keypath), "-N", ""],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            emit_output({
                "ok": False,
                "data": {"stderr": result.stderr},
                "error": f"ssh-keygen failed (exit {result.returncode})",
            }, args.format, title="SSH Key Generation")
            return EXIT_INTERNAL

        pubkey_path = keypath.with_suffix(".pub")
        pubkey = pubkey_path.read_text().strip()
        privkey_path = keypath

        emit_output({
            "ok": True,
            "data": {
                "private_key": str(privkey_path),
                "public_key": str(pubkey_path),
                "public_key_value": pubkey,
                "comment": comment,
            },
        }, args.format, title="SSH Key Generated")

        logger.log(
            event="ssh_keygen",
            argv=sys.argv[1:],
            extra={
                "private_key": str(privkey_path),
                "public_key": str(pubkey_path),
            },
        )
        return EXIT_SUCCESS
    except FileNotFoundError:
        emit_output({
            "ok": False,
            "error": "ssh-keygen not found on this system",
        }, args.format, title="SSH Key Generation")
        return EXIT_INTERNAL




def cmd_lxc_fix_console(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/lxc/{vmid}/config"
    resp = client.get(endpoint)
    logger.log(
        event="api_call",
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
        emit_output({"ok": False, "data": resp.get("data"), "error": resp.get("error")}, args.format, title=f"LXC {vmid} Config")
        return _exit_code_for(resp)
    data = resp.get("data") or {}
    ostype = str(data.get("ostype", ""))
    ostemplate = str(data.get("ostemplate", args.ostemplate or ""))
    if ostype == "debian" or _is_debian_13_template(ostemplate):
        print(_lxc_console_fix_instructions(vmid))
    else:
        print(render_markdown(f"LXC {vmid} Console Fix", [("Note", "This template is not known to need the Debian 13 console-getty workaround.")]))
    return EXIT_SUCCESS


def cmd_lxc_delete(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    endpoint = f"/nodes/{node}/lxc/{vmid}"

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
    emit_output(result, args.format, title=f"LXC {vmid} Delete")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, args.wait_timeout, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_lxc_snapshot_list(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    endpoint = f"/nodes/{node}/lxc/{args.vmid}/snapshot"
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
    emit_output(result, args.format, title=f"LXC {args.vmid} Snapshots")
    return _exit_code_for(resp)


def _lxc_snapshot_change(
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
        endpoint = f"/nodes/{node}/lxc/{vmid}/snapshot"
    else:
        endpoint = f"/nodes/{node}/lxc/{vmid}/snapshot/{name}"

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
    emit_output(result, args.format, title=f"LXC {vmid} Snapshot {action.title()}")
    if resp["ok"] and task_id and getattr(args, "wait", False):
        return _wait_for_task(node, task_id, 120, args, config, client, logger)
    return _exit_code_for(resp)


def cmd_lxc_snapshot_create(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _lxc_snapshot_change(args, config, client, logger, "create")


def cmd_lxc_snapshot_delete(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    return _lxc_snapshot_change(args, config, client, logger, "delete", destructive=True)

"""CLI entrypoint: environment resolution, dispatch, and error handling."""

import sys
from typing import Any

from ..common.config import Config, load_env, resolve_env_path
from ..common.constants import (
    EXIT_API,
    EXIT_INTERNAL,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
    REPO_ROOT,
)
from ..common.logging_ import Logger
from ..common.client import ProxmoxClient
from ..common.redact import redact_string
from ..common.render import emit_error
from .parser import build_parser
from . import registry
from .commands._shared import _require_config
from .commands import cluster, exec as guest_exec, lxc, storage, tasks, vm

# Bind every handler into this module's namespace so the dispatch body below
# (extracted verbatim from the legacy flat script) resolves them unchanged.
globals().update(registry.HANDLERS)
globals().update(registry._SUPPORT)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_VALIDATION

    env_path = resolve_env_path(getattr(args, "env_file", None) or getattr(args, "global_env_file", None))
    env = load_env(env_path)
    config = Config(env)
    config.env_path = env_path

    # Apply CLI overrides
    requested_format = getattr(args, "format", None) or getattr(args, "global_format", None)
    if requested_format:
        config.output_format = requested_format
    # Handlers consistently consume args.format; make the configured default effective.
    args.format = config.output_format
    if getattr(args, "timeout", None):
        config.timeout_seconds = args.timeout
    if getattr(args, "dry_run", False):
        args.execute = False
        args.force = False

    logger = Logger(config.log_dir, no_log=args.no_log, config=config)
    client = ProxmoxClient(config, logger)

    try:
        resource = args.resource
        action = getattr(args, "action", None)

        if resource == "doctor":
            return cmd_doctor(args, config, client, logger)

        if resource == "commands":
            return cmd_commands(args, config, client, logger)

        # Local metadata and key planning do not need Proxmox credentials.
        local_only = resource in {"commands", "logs"} or (resource == "lxc" and action == "ssh-keygen")
        if not local_only:
            err = _require_config(config, logger)
            if err is not None:
                return err

        if resource == "health" or resource == "status":
            return cmd_health(args, config, client, logger)
        if resource == "resolve":
            return cmd_resolve(args, config, client, logger)
        if resource == "nodes" and (action is None or action == "list"):
            return cmd_nodes_list(args, config, client, logger)
        if resource == "node" and action == "status":
            return cmd_node_status(args, config, client, logger)
        if resource == "vms" and (action is None or action == "list"):
            return cmd_vms_list(args, config, client, logger)
        if resource == "vms" and action == "next-id":
            return cmd_next_id(args, config, client, logger, title="Next Free VM/CT ID")
        if resource == "vm" or resource == "vm-status":
            if resource == "vm-status":
                action = "status"
            if action == "status":
                return cmd_vm_status(args, config, client, logger)
            if action == "config":
                return cmd_vm_config(args, config, client, logger)
            if action == "guest-network":
                return cmd_vm_guest_network(args, config, client, logger)
            if action == "exec":
                return cmd_vm_exec(args, config, client, logger)
            if action == "start":
                return cmd_vm_start(args, config, client, logger)
            if action == "shutdown":
                return cmd_vm_shutdown(args, config, client, logger)
            if action == "stop":
                return cmd_vm_stop(args, config, client, logger)
            if action == "reboot":
                return cmd_vm_reboot(args, config, client, logger)
            if action == "create":
                return cmd_vm_create(args, config, client, logger)
            if action == "clone":
                return cmd_vm_clone(args, config, client, logger)
            if action == "delete":
                return cmd_vm_delete(args, config, client, logger)
            if action == "snapshot":
                snap_action = getattr(args, "snapshot_action", None)
                if snap_action == "list":
                    return cmd_vm_snapshot_list(args, config, client, logger)
                if snap_action == "create":
                    return cmd_vm_snapshot_create(args, config, client, logger)
                if snap_action == "delete":
                    return cmd_vm_snapshot_delete(args, config, client, logger)
        if resource == "start-vm":
            return cmd_vm_start(args, config, client, logger)
        if resource == "reboot-vm":
            return cmd_vm_reboot(args, config, client, logger)
        if resource == "stop-vm":
            return cmd_vm_stop(args, config, client, logger)
        if resource == "lxcs" and (action is None or action == "list"):
            return cmd_lxcs_list(args, config, client, logger)
        if resource == "lxcs" and action == "next-id":
            return cmd_next_id(args, config, client, logger, title="Next Free VM/CT ID")
        if resource == "containers":
            return cmd_lxcs_list(args, config, client, logger)
        if resource == "container-status":
            return cmd_lxc_status(args, config, client, logger)
        if resource == "lxc":
            if action == "status":
                return cmd_lxc_status(args, config, client, logger)
            if action == "config":
                return cmd_lxc_config(args, config, client, logger)
            if action == "start":
                return cmd_lxc_start(args, config, client, logger)
            if action == "shutdown":
                return cmd_lxc_shutdown(args, config, client, logger)
            if action == "stop":
                return cmd_lxc_stop(args, config, client, logger)
            if action == "create":
                return cmd_lxc_create(args, config, client, logger)
            if action == "clone":
                return cmd_lxc_clone(args, config, client, logger)
            if action == "delete":
                return cmd_lxc_delete(args, config, client, logger)
            if action == "fix-console":
                return cmd_lxc_fix_console(args, config, client, logger)
            if action == "ssh-keygen":
                return cmd_lxc_ssh_keygen(args, config, client, logger)
            if action == "exec":
                return cmd_lxc_exec(args, config, client, logger)
            if action == "snapshot":
                snap_action = getattr(args, "snapshot_action", None)
                if snap_action == "list":
                    return cmd_lxc_snapshot_list(args, config, client, logger)
                if snap_action == "create":
                    return cmd_lxc_snapshot_create(args, config, client, logger)
                if snap_action == "delete":
                    return cmd_lxc_snapshot_delete(args, config, client, logger)
        if resource == "ssh":
            if action == "exec":
                return cmd_ssh_exec(args, config, client, logger)
        if resource == "templates":
            if action == "registry":
                return cmd_templates_registry(args, config, client, logger)
            if action == "download":
                return cmd_templates_download(args, config, client, logger)
        if resource == "storage":
            if action == "list":
                return cmd_storage_list(args, config, client, logger)
            if action == "content":
                return cmd_storage_content(args, config, client, logger)
            if action == "templates":
                return cmd_storage_templates(args, config, client, logger)
            if action == "template-download":
                return cmd_storage_template_download(args, config, client, logger)
        if resource == "network" and action == "list":
            return cmd_network_list(args, config, client, logger)
        if resource == "tasks" and action == "recent":
            return cmd_tasks_recent(args, config, client, logger)
        if resource == "task":
            if action == "status":
                return cmd_task_status(args, config, client, logger)
            if action == "wait":
                return cmd_task_wait(args, config, client, logger)
        if resource == "logs":
            if action == "latest":
                return cmd_logs_latest(args, config, client, logger)
            if action == "path":
                return cmd_logs_path(args, config, client, logger)
            if action == "tail":
                return cmd_logs_tail(args, config, client, logger)
        if resource == "api":
            return cmd_api_raw(args, config, client, logger)

        emit_error("unknown_command", f"Unknown command: {resource} {action}", "", logger.file_path)
        return EXIT_VALIDATION
    except Exception as exc:
        msg = str(exc)
        if config.token_secret:
            msg = redact_string(msg, config)
        logger.log(
            event="internal_error",
            argv=sys.argv[1:],
            ok=False,
            error=msg,
        )
        emit_error("internal_error", msg, "Check the log for details.", logger.file_path)
        return EXIT_INTERNAL
    finally:
        logger.symlink_latest()

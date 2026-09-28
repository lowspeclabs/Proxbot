"""Argparse construction for every CLI subcommand."""

import argparse

from ..common.config import _positive_int


def _add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--format", choices=["md", "json", "table"], default=None, help="Output format")
    parser.add_argument("--env-file", help="Configuration file (default: .env in the current directory, then the installed skill)")
    parser.add_argument("--no-log", action="store_true", help="Disable logging")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    return parser


def _add_timeout_arg(parser: argparse.ArgumentParser, default: int | None = None) -> argparse.ArgumentParser:
    parser.add_argument("--request-timeout", "--timeout", dest="timeout", type=int, default=default, help="API request timeout in seconds")
    return parser


def _add_node_arg(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--node", help="Node name")
    return parser


def _add_vmid_arg(parser: argparse.ArgumentParser, required: bool = True) -> argparse.ArgumentParser:
    parser.add_argument("--vmid", type=int, required=required, help="VM/LXC ID")
    return parser


def _add_node_vmid_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    _add_node_arg(parser)
    _add_vmid_arg(parser)
    return parser


def _add_list_filter_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--status", help="Only return rows with this status")
    parser.add_argument("--limit", type=_positive_int, help="Maximum number of rows to return")
    return parser


def _add_write_guard_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--execute", "--yes", action="store_true", help="Allow state-changing actions")
    parser.add_argument("--wait", action="store_true", help="Wait for task to complete")
    parser.add_argument("--wait-timeout", type=int, default=120, help="Maximum task wait time in seconds")
    return parser


def _add_destructive_guard_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--execute", "--yes", action="store_true", help="Allow state-changing actions")
    parser.add_argument("--force", action="store_true", help="Confirm destructive intent")
    parser.add_argument("--confirm", help="Confirm resource ID for destructive actions")
    parser.add_argument("--wait", action="store_true", help="Wait for task to complete")
    parser.add_argument("--wait-timeout", type=int, default=120, help="Maximum task wait time in seconds")
    return parser


def _cmd(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Apply common args to a read-only command subparser."""
    _add_common_args(parser)
    _add_timeout_arg(parser)
    return parser


def _common_parent_parser() -> argparse.ArgumentParser:
    """Return a parent parser with common read-only args for use with argparse parents."""
    parent = argparse.ArgumentParser(add_help=False)
    _add_common_args(parent)
    _add_timeout_arg(parent)
    return parent


def _node_list_parent_parser() -> argparse.ArgumentParser:
    """Return a parent parser with common args plus --node for list-style resources."""
    parent = _common_parent_parser()
    _add_node_arg(parent)
    return parent


def _write_cmd(parser: argparse.ArgumentParser, destructive: bool = False) -> argparse.ArgumentParser:
    """Apply common args and safety guards to a write command subparser."""
    _add_common_args(parser)
    _add_timeout_arg(parser)
    if destructive:
        _add_destructive_guard_args(parser)
    else:
        _add_write_guard_args(parser)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxmox-cli",
        description="Agent-friendly CLI wrapper for Proxmox VE.",
        epilog="Common options (per-command): --format {md,json,table}, --request-timeout SECONDS, --no-log, --dry-run, --execute, --force, --confirm ID",
    )
    parser.add_argument("--format", dest="global_format", choices=["md", "json", "table"], help="Output format (may appear before the command)")
    parser.add_argument("--env-file", dest="global_env_file", help="Configuration file (may appear before the command)")

    sub = parser.add_subparsers(dest="resource", required=True)

    common_parent = _common_parent_parser()
    node_list_parent = _node_list_parent_parser()

    # Meta
    _cmd(sub.add_parser("commands", help="List canonical commands and their required arguments"))
    resolve = _cmd(sub.add_parser("resolve", help="Locate a VM or container by ID across cluster nodes"))
    resolve.add_argument("--vmid", type=int, required=True, help="VM/LXC ID")
    resolve.add_argument("--kind", choices=["vm", "lxc"], help="Expected resource type")
    doctor = _cmd(sub.add_parser("doctor", help="Check config and API connectivity"))
    doctor.add_argument("--live", action="store_true", help="Run a live API connectivity test")
    doctor.add_argument("--node-access", action="store_true", help="Also probe node SSH and report whether pct exec is usable (implies a live check)")
    _cmd(sub.add_parser("health", help="Show Proxmox health summary"))

    # Nodes
    nodes = sub.add_parser("nodes", help="Node commands", parents=[node_list_parent])
    nodes_sub = nodes.add_subparsers(dest="action", required=False)
    nodes_sub.add_parser("list", help="List nodes", parents=[node_list_parent])

    node = sub.add_parser("node", help="Single node commands")
    node_sub = node.add_subparsers(dest="action", required=True)
    _cmd(_add_node_arg(node_sub.add_parser("status", help="Show node status")))

    # VMs
    vms = sub.add_parser("vms", help="VM list commands", parents=[node_list_parent])
    vms_sub = vms.add_subparsers(dest="action", required=False)
    vms_list = vms_sub.add_parser("list", help="List VMs", parents=[node_list_parent])
    _add_list_filter_args(vms_list)
    vms_next_id = _cmd(vms_sub.add_parser("next-id", help="Get next free VM/CT ID"))
    vms_next_id.add_argument("--vmid", type=int, help="Suggested starting ID")

    vm = sub.add_parser("vm", help="Single VM commands")
    vm_sub = vm.add_subparsers(dest="action", required=True)
    _cmd(_add_node_vmid_args(vm_sub.add_parser("status", help="Show VM status")))
    _cmd(_add_node_vmid_args(vm_sub.add_parser("config", help="Show VM config")))
    _cmd(_add_node_vmid_args(vm_sub.add_parser("guest-network", help="List guest interfaces through QEMU guest agent")))
    vm_exec = _write_cmd(_add_node_vmid_args(vm_sub.add_parser("exec", help="Execute command inside VM via QEMU guest agent (requires --execute)")))
    vm_exec.add_argument("--command", required=True, help="Command to execute inside VM")
    _write_cmd(_add_node_vmid_args(vm_sub.add_parser("start", help="Start VM (requires --execute)")))
    _write_cmd(_add_node_vmid_args(vm_sub.add_parser("shutdown", help="Shutdown VM (requires --execute)")))
    _write_cmd(_add_node_vmid_args(vm_sub.add_parser("stop", help="Stop VM (requires --execute --force --confirm)")), destructive=True)
    _write_cmd(_add_node_vmid_args(vm_sub.add_parser("reboot", help="Reboot VM (requires --execute)")))
    vm_create = _write_cmd(_add_node_vmid_args(vm_sub.add_parser("create", help="Create VM (requires --execute)")))
    vm_create.add_argument("--name", required=True, help="VM name")
    vm_create.add_argument("--storage", help="Storage pool")
    vm_create.add_argument("--memory", type=int, help="Memory in MB")
    vm_create.add_argument("--cores", type=int, help="Number of cores")
    vm_create.add_argument("--sockets", type=int, help="Number of CPU sockets")
    vm_create.add_argument("--cpu", help="CPU type")
    vm_create.add_argument("--ostype", help="OS type (e.g., l26)")
    vm_create.add_argument("--net0", help="Network interface (e.g., virtio,bridge=vmbr0)")
    vm_create.add_argument("--scsihw", help="SCSI controller type")
    vm_create.add_argument("--onboot", action=argparse.BooleanOptionalAction, default=None, help="Start VM on boot (default: Proxmox default)")
    vm_create.add_argument("--data", help="JSON body for extra parameters")
    vm_clone = _write_cmd(_add_node_vmid_args(vm_sub.add_parser("clone", help="Clone VM or VM template (requires --execute)")))
    vm_clone.add_argument("--new-vmid", type=int, required=True, help="New VM ID (use vms next-id)")
    vm_clone.add_argument("--name", help="Name for the clone")
    vm_clone.add_argument("--storage", help="Target storage pool for full clone")
    vm_clone.add_argument("--pool", help="Target pool")
    vm_clone.add_argument("--full", action=argparse.BooleanOptionalAction, default=None, help="Full clone (default: Proxmox default)")
    vm_clone.add_argument("--snapname", help="Snapshot to clone from")
    vm_clone.add_argument("--data", help="JSON body for extra parameters")
    vm_delete = _write_cmd(_add_node_vmid_args(vm_sub.add_parser("delete", help="Delete VM (requires --execute --force --confirm)")), destructive=True)

    vm_snapshot = vm_sub.add_parser("snapshot", help="VM snapshot commands")
    vm_snapshot_sub = vm_snapshot.add_subparsers(dest="snapshot_action", required=True)
    _cmd(_add_node_vmid_args(vm_snapshot_sub.add_parser("list", help="List VM snapshots")))
    vm_snap_create = _write_cmd(_add_node_vmid_args(vm_snapshot_sub.add_parser("create", help="Create VM snapshot (requires --execute)")))
    vm_snap_create.add_argument("--name", required=True, help="Snapshot name")
    vm_snap_delete = _write_cmd(_add_node_vmid_args(vm_snapshot_sub.add_parser("delete", help="Delete VM snapshot (requires --execute --force --confirm)")), destructive=True)
    vm_snap_delete.add_argument("--name", required=True, help="Snapshot name")

    # LXCs
    lxcs = sub.add_parser("lxcs", help="LXC list commands", parents=[node_list_parent])
    lxcs_sub = lxcs.add_subparsers(dest="action", required=False)
    lxcs_list = lxcs_sub.add_parser("list", help="List LXCs", parents=[node_list_parent])
    _add_list_filter_args(lxcs_list)
    lxcs_next_id = _cmd(lxcs_sub.add_parser("next-id", help="Get next free VM/CT ID"))
    lxcs_next_id.add_argument("--vmid", type=int, help="Suggested starting ID")

    lxc = sub.add_parser("lxc", help="Single LXC commands")
    lxc_sub = lxc.add_subparsers(dest="action", required=True)
    _cmd(_add_node_vmid_args(lxc_sub.add_parser("status", help="Show LXC status")))
    _cmd(_add_node_vmid_args(lxc_sub.add_parser("config", help="Show LXC config")))
    _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("start", help="Start LXC (requires --execute)")))
    _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("shutdown", help="Shutdown LXC (requires --execute)")))
    _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("stop", help="Stop LXC (requires --execute --force --confirm)")), destructive=True)
    lxc_create = _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("create", help="Create LXC (requires --execute)")))
    lxc_create.add_argument("--hostname", required=True, help="Container hostname")
    lxc_create.add_argument("--ostemplate", required=True, help="OS template")
    lxc_create.add_argument("--storage", required=True, help="Storage pool")
    lxc_create.add_argument("--features", default=None, help="LXC features (e.g., nesting=1). Omit to use default.")
    lxc_create.add_argument("--unprivileged", action=argparse.BooleanOptionalAction, default=True, help="Create an unprivileged container (default: True)")
    lxc_create.add_argument("--password", help="Root password")
    lxc_create.add_argument("--cores", type=int, help="Number of cores")
    lxc_create.add_argument("--memory", type=int, help="Memory in MB")
    lxc_create.add_argument("--swap", type=int, help="Swap in MB")
    lxc_create.add_argument("--net0", help="Network interface (e.g., name=eth0,bridge=vmbr0,ip=dhcp)")
    lxc_create.add_argument("--rootfs", help="Root filesystem (e.g., local-lvm:8)")
    lxc_create.add_argument("--nameserver", help="DNS server(s) (e.g., 1.1.1.1 or 1.1.1.1,8.8.8.8)")
    lxc_create.add_argument("--onboot", action=argparse.BooleanOptionalAction, default=None, help="Start container on boot (default: Proxmox default)")
    lxc_create.add_argument("--ssh-public-keys", help="SSH public key(s) to inject into the container")
    lxc_create.add_argument("--ssh-keygen", action="store_true", help="Generate a new SSH key pair and inject the public key")
    lxc_create.add_argument("--ssh-key-path", help="Path to save generated SSH key (default: ./.lxc-ssh-key-<VMID>)")
    lxc_create.add_argument("--data", help="JSON body for extra parameters")
    lxc_create.add_argument("--as-template", action="store_true", help="Convert the new container to a template after creation (for building a keyed base image that clones inherit)")
    lxc_clone = _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("clone", help="Clone LXC or converted LXC template (requires --execute)")))
    lxc_clone.add_argument("--new-vmid", type=int, required=True, help="New container ID (use lxcs next-id)")
    lxc_clone.add_argument("--hostname", help="Hostname for the clone")
    lxc_clone.add_argument("--storage", help="Target storage pool for the clone rootfs")
    lxc_clone.add_argument("--pool", help="Target pool")
    lxc_clone.add_argument("--full", action=argparse.BooleanOptionalAction, default=None, help="Full clone (default: Proxmox default)")
    lxc_clone.add_argument("--snapname", help="Snapshot to clone from")
    lxc_clone.add_argument("--data", help="JSON body for extra parameters")
    lxc_exec = _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("exec", help="Execute command inside LXC container (requires --execute)")))
    lxc_exec.add_argument("--command", required=True, help="Shell command to execute inside container")
    lxc_exec.add_argument("--user", default="root", help="Username for console/SSH login (default: root)")
    lxc_exec.add_argument("--password", help="Password for container console login")
    lxc_exec.add_argument("--key-file", help="SSH private key path for key injection / fallback")
    lxc_exec.add_argument("--no-auto-inject-key", action="store_true", help="Do not automatically inject SSH key after console login")
    lxc_exec.add_argument("--console", action="store_true", help="Allow fallback to the container console (termproxy). The console is a single shared pty, so only pass this when you have confirmed no other session is attached.")
    lxc_delete = _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("delete", help="Delete LXC (requires --execute --force --confirm)")), destructive=True)
    lxc_pct_exec = _write_cmd(_add_node_vmid_args(lxc_sub.add_parser("pct-exec", help="Run a command inside an LXC via the node (pct exec over SSH; requires --execute)")))
    lxc_pct_exec.add_argument("--command", required=True, help="Shell command to run inside the container")
    lxc_fix_console = _cmd(_add_node_vmid_args(lxc_sub.add_parser("fix-console", help="Print Debian 13 LXC console workaround")))
    lxc_fix_console.add_argument("--ostemplate", help="OS template (if not stored in config)")

    lxc_ssh_keygen = _write_cmd(lxc_sub.add_parser("ssh-keygen", help="Generate an SSH key pair for LXC access (requires --execute)"))
    lxc_ssh_keygen.add_argument("--vmid", type=int, help="LXC VMID to include in the key comment")
    lxc_ssh_keygen.add_argument("--keyfile", help="Path to save the key (default: ./.lxc-ssh-key)")
    lxc_ssh_keygen.add_argument("--comment", help="SSH key comment")
    lxc_ssh_keygen.add_argument("--force", action="store_true", help="Overwrite existing key file")

    # SSH commands (for VMs, LXCs, and remote hosts)
    ssh_parser = sub.add_parser("ssh", help="SSH command execution")
    ssh_sub = ssh_parser.add_subparsers(dest="action", required=True)
    ssh_exec = _write_cmd(ssh_sub.add_parser("exec", help="Execute command over SSH on VM, LXC, or host (requires --execute)"))
    ssh_exec.add_argument("--host", help="Target host IP or domain name")
    ssh_exec.add_argument("--node", help="Proxmox node (for VM/LXC IP resolution)")
    ssh_exec.add_argument("--vmid", type=int, help="Target VM or LXC ID")
    ssh_exec.add_argument("--kind", choices=["vm", "lxc"], help="Resource type for ID resolution")
    ssh_exec.add_argument("--command", required=True, help="Shell command to execute over SSH")
    ssh_exec.add_argument("--user", default="root", help="SSH user (default: root)")
    ssh_exec.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    ssh_exec.add_argument("--key-file", "-i", help="Path to SSH private key")

    lxc_snapshot = lxc_sub.add_parser("snapshot", help="LXC snapshot commands")
    lxc_snapshot_sub = lxc_snapshot.add_subparsers(dest="snapshot_action", required=True)
    _cmd(_add_node_vmid_args(lxc_snapshot_sub.add_parser("list", help="List LXC snapshots")))
    lxc_snap_create = _write_cmd(_add_node_vmid_args(lxc_snapshot_sub.add_parser("create", help="Create LXC snapshot (requires --execute)")))
    lxc_snap_create.add_argument("--name", required=True, help="Snapshot name")
    lxc_snap_delete = _write_cmd(_add_node_vmid_args(lxc_snapshot_sub.add_parser("delete", help="Delete LXC snapshot (requires --execute --force --confirm)")), destructive=True)
    lxc_snap_delete.add_argument("--name", required=True, help="Snapshot name")

    # Template registry (upstream aplinfo, cf. `pveam available/download`)
    templates = sub.add_parser("templates", help="Upstream container template registry commands")
    templates_sub = templates.add_subparsers(dest="action", required=True)
    registry_list = _cmd(_add_node_arg(templates_sub.add_parser("registry", help="Browse upstream LXC template registry (pveam available)")))
    registry_list.add_argument("--section", help="Filter by registry section (e.g. system, turnkeylinux, mail)")
    registry_list.add_argument("--search", help="Substring filter over template, package, headline, description, os")
    registry_list.add_argument("--limit", type=_positive_int, help="Maximum number of rows to return")
    registry_download = _write_cmd(_add_node_arg(templates_sub.add_parser("download", help="Download a registry template by name (pveam download, requires --execute)")))
    registry_download.add_argument("--storage", required=True, help="Storage where the template will be stored")
    registry_download.add_argument("--template", required=True, help="Registry template name (see templates registry)")

    # Storage
    storage = sub.add_parser("storage", help="Storage commands")
    storage_sub = storage.add_subparsers(dest="action", required=True)
    _cmd(_add_node_arg(storage_sub.add_parser("list", help="List storage")))
    storage_content = _cmd(_add_node_arg(storage_sub.add_parser("content", help="Show storage content")))
    storage_content.add_argument("--storage", required=True, help="Storage name")
    storage_templates = _cmd(_add_node_arg(storage_sub.add_parser("templates", help="List LXC templates on storage")))
    storage_templates.add_argument("--storage", required=True, help="Storage name")
    storage_download = _write_cmd(_add_node_arg(storage_sub.add_parser("template-download", help="Download LXC template (requires --execute)")))
    storage_download.add_argument("--storage", required=True, help="Storage name")
    storage_download.add_argument("--url", required=True, help="Template URL")
    storage_download.add_argument("--filename", required=True, help="Template filename")
    storage_download.add_argument("--checksum", help="Optional checksum")
    storage_download.add_argument("--data", help="JSON body for extra parameters")

    # Network
    network = sub.add_parser("network", help="Network commands")
    network_sub = network.add_subparsers(dest="action", required=True)
    _cmd(_add_node_arg(network_sub.add_parser("list", help="List networks")))

    # Tasks
    tasks = sub.add_parser("tasks", help="Task commands")
    tasks_sub = tasks.add_subparsers(dest="action", required=True)
    tasks_recent = _cmd(_add_node_arg(tasks_sub.add_parser("recent", help="List recent tasks")))
    _add_list_filter_args(tasks_recent)

    task = sub.add_parser("task", help="Single task commands")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_status = _cmd(_add_node_arg(task_sub.add_parser("status", help="Show task status")))
    task_status.add_argument("--upid", required=True, help="Task UPID")
    task_wait = _add_common_args(_add_node_arg(task_sub.add_parser("wait", help="Wait for task to complete")))
    task_wait.add_argument("--upid", required=True, help="Task UPID")
    task_wait.add_argument("--wait-timeout", "--timeout", dest="wait_timeout", type=int, default=120, help="Maximum task wait time in seconds")

    # Logs
    logs = sub.add_parser("logs", help="Log inspection commands")
    logs_sub = logs.add_subparsers(dest="action", required=True)
    _cmd(logs_sub.add_parser("latest", help="Print latest log file path"))
    _cmd(logs_sub.add_parser("path", help="Print log directory path"))
    logs_tail = _cmd(logs_sub.add_parser("tail", help="Tail latest log file"))
    logs_tail.add_argument("--lines", type=int, default=20, help="Number of lines")

    # Raw API
    api = sub.add_parser("api", help="Raw API commands")
    api.add_argument("method", choices=["get", "post", "put", "delete"], help="HTTP method")
    api.add_argument("endpoint", help="API endpoint path")
    _write_cmd(api)
    api.add_argument("--force", action="store_true", help="Confirm a raw API DELETE")
    api.add_argument("--confirm", help="Confirm the exact endpoint for a raw API DELETE")
    api.add_argument("--node", help="Task node when using --wait and the endpoint is not node-scoped")
    api.add_argument("--data", help="JSON body for POST/PUT")

    # Prompt.md compatibility aliases
    _cmd(sub.add_parser("status", help="Legacy alias for health"))
    sub.add_parser("containers", help="Legacy alias for lxcs list", parents=[node_list_parent])
    _cmd(_add_node_vmid_args(sub.add_parser("vm-status", help="Legacy alias for vm status")))
    _cmd(_add_node_vmid_args(sub.add_parser("container-status", help="Legacy alias for lxc status")))
    _write_cmd(_add_node_vmid_args(sub.add_parser("start-vm", help="Legacy alias for vm start")))
    _write_cmd(_add_node_vmid_args(sub.add_parser("reboot-vm", help="Legacy alias for vm reboot")))
    _write_cmd(_add_node_vmid_args(sub.add_parser("stop-vm", help="Legacy alias for vm stop")), destructive=True)

    return parser

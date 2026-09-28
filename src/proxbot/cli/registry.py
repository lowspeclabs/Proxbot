"""Command handler registry: maps CLI verbs to handler functions.

The legacy flat script exposed every ``cmd_*`` function as a module attribute and
tests patched them via ``cli.<name>``. To preserve that contract, handlers are
imported here and re-exported from :mod:`proxmox.cli`.
"""

from .commands import cluster, exec as guest_exec, lxc, nodessh, storage, tasks, vm  # noqa: F401

_HANDLERS = (
    cluster,
    guest_exec,
    lxc,
    nodessh,
    storage,
    tasks,
    vm,
)

# Every public ``cmd_*`` symbol from the modules above.
HANDLERS = {
    name: obj
    for module in _HANDLERS
    for name, obj in vars(module).items()
    if name.startswith("cmd_") and callable(obj)
}

# Legacy private helpers that the flat script exposed (and that tests/console use).
# Legacy private helpers that the flat script exposed, and that the console
# broker reaches through `cli.<name>` at runtime.
from .commands._shared import (  # noqa: E402,F401
    _exit_code_for,
    _extract_task,
    _require_config,
    _wait_for_task,
)

_SUPPORT = {
    name: obj
    for module in _HANDLERS
    for name, obj in vars(module).items()
    if name in {
        "_exit_code_for",
        "_extract_task",
        "_require_config",
        "_wait_for_task",
        "_resolve_guest_ip",
        "_run_ssh_command",
        "_parse_console_output",
        "lxc_exec",
        "assemble_pct_exec",
        "probe_node_ssh",
        "filter_registry_rows",
        "filter_rows",
    }
}

globals().update(HANDLERS)
globals().update(_SUPPORT)

#!/usr/bin/env python3
"""
Proxmox Agent Harness CLI.

Stable entrypoint for the agent-friendly Proxmox VE wrapper. The implementation
now lives in the `proxmox` package (`src/proxbot/`); this module re-exports the
public surface so existing callers, tests, and the console broker are unaffected.

  common/  config, logging, redaction, API client, rendering, safety guards
  cli/     parser + domain command modules + dispatch
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from proxbot.cli import *  # noqa: F401,F403
from proxbot.cli import main  # noqa: F401

# `import *` skips underscore-prefixed names, but the console broker and tests
# reach these on the loaded `proxmox_cli` module at runtime.
from proxbot.cli import (  # noqa: F401,E402
    _exit_code_for,
    _extract_task,
    _require_config,
    _wait_for_task,
    _resolve_guest_ip,
    _run_ssh_command,
    lxc_exec,
    filter_registry_rows,
    filter_rows,
    load_env,
    resolve_env_path,
    redact_data,
    redact_string,
    redact_value,
    _redact_argv,
    _redact_command_str,
    latest_log_file,
    render_json,
    render_table,
    render_markdown,
    render_error_markdown,
    requested_format_from_argv,
    emit_dry_run,
    request_plan,
)

if __name__ == "__main__":
    sys.exit(main())

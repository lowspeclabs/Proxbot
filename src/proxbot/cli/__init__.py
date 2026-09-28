"""Agent-facing Proxmox CLI implementation (thin façade over `common` + `commands`)."""

from ..common.config import Config, load_env, resolve_env_path, strip_ansi  # noqa: F401
from ..common.constants import (  # noqa: F401
    DEFAULT_LOG_DIR,
    ENV_PATH,
    EXIT_API,
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_INTERNAL,
    EXIT_NOOP,
    EXIT_SAFETY,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    EXIT_VALIDATION,
    REPO_ROOT,
    REDACTED,
    SENSITIVE_KEYS_RE,
    STRIP_ANSI_RE,
)
from ..common.redact import (  # noqa: F401
    _redact_argv,
    _redact_command_str,
    redact_data,
    redact_string,
    redact_value,
)
from ..common.logging_ import Logger, latest_log_file  # noqa: F401
from ..common.client import ProxmoxClient  # noqa: F401
from ..common.render import (  # noqa: F401
    emit_error,
    emit_output,
    filter_rows,
    render_error_markdown,
    render_json,
    render_markdown,
    render_output,
    render_table,
    requested_format_from_argv,
)
from ..common.safety import (  # noqa: F401
    emit_dry_run,
    request_plan,
    require_destructive,
    require_execute,
)
from .parser import build_parser  # noqa: F401
from .commands._shared import (  # noqa: F401
    _exit_code_for,
    _extract_task,
    _require_config,
    _wait_for_task,
)
from .commands.exec import _resolve_guest_ip, _run_ssh_command  # noqa: F401
from .registry import *  # noqa: F401,F403
from .main import main  # noqa: F401

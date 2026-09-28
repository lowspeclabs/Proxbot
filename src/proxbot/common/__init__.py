"""Shared plumbing reused by the CLI, chat assistant, and console broker."""

from .constants import (  # noqa: F401
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
    STRIP_ANSI_RE,
)
from .config import Config, load_env, resolve_env_path, strip_ansi  # noqa: F401
from .redact import redact_data, redact_string, redact_value  # noqa: F401
from .logging_ import Logger, latest_log_file  # noqa: F401
from .client import ProxmoxClient  # noqa: F401
from .render import emit_error, emit_output, filter_rows, render_output  # noqa: F401
from .safety import emit_dry_run, request_plan, require_destructive, require_execute  # noqa: F401

"""Shared exit codes, paths, and redaction constants."""

import pathlib
import re

# Repository layout (src/proxbot/common/constants.py -> repo root)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_LOG_DIR = REPO_ROOT / "scripts" / "logs"

EXIT_SUCCESS = 0
EXIT_VALIDATION = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_API = 4
EXIT_TIMEOUT = 5
EXIT_NOOP = 6
EXIT_SAFETY = 10
EXIT_INTERNAL = 99

REDACTED = "***REDACTED***"
SENSITIVE_KEYS_RE = re.compile(r"(?i)(secret|token|password|key)")

STRIP_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

"""`.env` loading and the shared Proxmox connection config."""

import argparse
import pathlib
import re

from .constants import DEFAULT_LOG_DIR, ENV_PATH, REPO_ROOT, REDACTED, SENSITIVE_KEYS_RE, STRIP_ANSI_RE

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return STRIP_ANSI_RE.sub("", text)


class Config:
    """Holds validated Proxmox connection configuration."""

    def __init__(self, raw: dict[str, str]):
        self.raw = raw
        self.env_path = ENV_PATH
        # Support both spec format (PROXMOX_API_URL) and prompt.md format (PROXMOX_HOST + PROXMOX_PORT).
        api_url = (raw.get("PROXMOX_API_URL") or "").strip().rstrip("/")
        host = (raw.get("PROXMOX_HOST") or "").strip()
        port = _parse_int(raw.get("PROXMOX_PORT", "8006"), 8006)
        if api_url:
            self.api_url = api_url
        elif host:
            self.api_url = f"https://{host}:{port}/api2/json"
        else:
            self.api_url = ""
        # Support both spec format (full token id) and prompt.md format (user + token id).
        token_id = (raw.get("PROXMOX_API_TOKEN_ID") or "").strip()
        api_user = (raw.get("PROXMOX_API_USER") or "").strip()
        if token_id and "!" in token_id:
            self.token_id = token_id
        elif token_id and api_user:
            self.token_id = f"{api_user}!{token_id}"
        else:
            self.token_id = token_id
        self.token_secret = (raw.get("PROXMOX_API_TOKEN_SECRET") or "").strip()
        self.verify_ssl = _parse_bool(raw.get("PROXMOX_VERIFY_SSL", "true"))
        self.timeout_seconds = _parse_int(raw.get("PROXMOX_TIMEOUT_SECONDS", "30"), 30)
        self.default_node = (raw.get("PROXMOX_DEFAULT_NODE") or "").strip() or None
        self.output_format = (raw.get("PROXMOX_OUTPUT_FORMAT") or "md").strip().lower()
        log_dir = pathlib.Path(raw.get("PROXMOX_LOG_DIR") or DEFAULT_LOG_DIR)
        self.log_dir = log_dir if log_dir.is_absolute() else REPO_ROOT / log_dir

    @property
    def ok(self) -> bool:
        return bool(self.api_url and self.token_id and self.token_secret)

    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.api_url:
            missing.append("PROXMOX_API_URL")
        if not self.token_id:
            missing.append("PROXMOX_API_TOKEN_ID")
        if not self.token_secret:
            missing.append("PROXMOX_API_TOKEN_SECRET")
        return missing

    def auth_header(self) -> str:
        return f"PVEAPIToken={self.token_id}={self.token_secret}"

    def redacted_header(self) -> str:
        return f"PVEAPIToken={self.token_id}={REDACTED}"


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def load_env(path: pathlib.Path = ENV_PATH) -> dict[str, str]:
    """Parse a simple KEY=VALUE `.env` file, skipping comments and blanks."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def resolve_env_path(value: str | None = None) -> pathlib.Path:
    """Choose an explicit config file, then a Proxmox cwd file, then the install."""
    if value:
        path = pathlib.Path(value).expanduser()
        return path if path.is_absolute() else pathlib.Path.cwd() / path
    cwd_env = pathlib.Path.cwd() / ".env"
    if cwd_env.exists():
        keys = set(load_env(cwd_env))
        if keys & {
            "PROXMOX_API_URL",
            "PROXMOX_API_TOKEN_ID",
            "PROXMOX_API_TOKEN_SECRET",
            "PROXMOX_HOST",
            "PROXMOX_API_USER",
        }:
            return cwd_env
    return ENV_PATH

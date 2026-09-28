"""Secret redaction for logs, output, and exception messages."""

import re
from typing import Any

from .config import Config
from .constants import REDACTED, SENSITIVE_KEYS_RE

def redact_value(key: str, value: Any) -> Any:
    """Redact values for keys that look sensitive."""
    if SENSITIVE_KEYS_RE.search(key):
        return REDACTED if value else value
    return value


def redact_data(data: Any) -> Any:
    """Recursively redact sensitive-looking keys from dict/list structures."""
    if isinstance(data, dict):
        return {k: REDACTED if SENSITIVE_KEYS_RE.search(k) else redact_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_data(v) for v in data]
    return data


def redact_string(text: str, config: Config) -> str:
    """Redact the token secret and Authorization header fragments in a string."""
    if not text:
        return text
    secret = config.token_secret
    if secret:
        text = text.replace(secret, REDACTED)
    text = text.replace(config.auth_header(), config.redacted_header())
    text = re.sub(r"(?i)Authorization:\s*PVEAPIToken=[^\s\"']+=[^\s\"']+", f"Authorization: {config.redacted_header()}", text)
    return text


SENSITIVE_ARGV_FLAGS = {
    "--password",
    "--token-secret",
    "--api-token-secret",
    "--secret",
    "--key",
    "--keys",
    "--private-key",
    "--text",
    "--actions",
    "--command",
}


def _redact_argv(argv: list[str], config: Config | None = None) -> list[str]:
    """Redact values that follow sensitive CLI flags, and apply string redaction."""
    redacted: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            redacted.append(REDACTED)
            skip_next = False
            continue
        if arg.lower() in SENSITIVE_ARGV_FLAGS:
            redacted.append(arg)
            skip_next = True
            continue
        redacted.append(arg)
    if config is not None:
        return [redact_string(str(a), config) for a in redacted]
    return redacted


def _redact_command_str(cmd: str) -> str:
    """Redact `--password <val>` (and sibling secrets) inside a joined command string.

    `emit_output` builds a human-readable `command` field via `" ".join(sys.argv)`.
    Without redaction the container password leaks into stdout/stderr JSON, chat
    session logs, and LLM context. This mirrors `_redact_argv` for the string form.
    """
    if not cmd:
        return cmd
    # --flag value  ->  --flag ***REDACTED***
    cmd = re.sub(
        r"(?i)(--password|--token-secret|--api-token-secret|--secret|--private-key|--text|--actions|--keys?)\s+(\"[^\"]*\"|'[^']*'|\S+)",
        r"\1 " + REDACTED,
        cmd,
    )
    # --flag=value  ->  --flag=***REDACTED***
    cmd = re.sub(
        r"(?i)(--password|--token-secret|--api-token-secret|--secret|--private-key|--text|--actions|--keys?)=(\"[^\"]*\"|'[^']*'|\S+)",
        r"\1=" + REDACTED,
        cmd,
    )
    return cmd

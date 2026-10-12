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
    "--node-password",
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

# Single source of truth for redacting secrets out of a joined command string.
# This pattern used to be duplicated in redact.py, agent.py and session.py,
# which is how --node-password was added to the argv list and then leaked through
# the string form. Extend this list, not a copy.
#
# Ordering: --node-password must precede --password in intent (a longer flag that
# ends in the same word). It is not strictly required for correctness because
# --password cannot match inside --node-password, but it documents the case.
_SECRET_FLAG_ALTERNATION = (
    r"--node-password|--password|--token-secret|--api-token-secret|--secret"
    r"|--private-key|--text|--actions|--keys?"
)
_SECRET_FLAG_RE = re.compile(
    rf"(?i)({_SECRET_FLAG_ALTERNATION})(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)"
)


def redact_command_secrets(cmd: str) -> str:
    """Redact the value following any secret-bearing flag in a command string."""
    if not cmd:
        return cmd
    return _SECRET_FLAG_RE.sub(rf"\1\2{REDACTED}", cmd)


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
    """Redact secret-bearing flag values inside a joined command string.

    `emit_output` builds a human-readable `command` field via `" ".join(sys.argv)`.
    Without redaction the container password leaks into stdout/stderr JSON, chat
    session logs, and LLM context. Delegates to the shared pattern so this
    cannot drift from the argv form or from the agent's own sanitizer.
    """
    return redact_command_secrets(cmd)

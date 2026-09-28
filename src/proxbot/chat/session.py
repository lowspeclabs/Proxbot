"""Structured JSONL chat session logger."""

import datetime
import json
import pathlib
import re
import sys
import uuid
from typing import Any, Dict, Optional

from ..common.redact import redact_command_secrets
from .markdown import RED, RESET


class SessionLogger:
    """Structured JSONL logger for chat sessions and tool executions."""

    SENSITIVE_KEYS = {"password", "token", "secret", "key", "token_secret", "api_key"}

    def __init__(self, log_dir: pathlib.Path, session_id: Optional[str] = None):
        self.log_dir = log_dir
        self.session_id = session_id or datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"chat-{self.session_id}.jsonl"

        self.stats = {
            "start_time": datetime.datetime.now().isoformat(),
            "user_prompts": 0,
            "tool_calls": 0,
            "tool_approvals": 0,
            "tool_denials": 0,
            "errors": 0,
        }

    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: ("***REDACTED***" if any(s in k.lower() for s in self.SENSITIVE_KEYS) else self._redact(v))
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [self._redact(item) for item in obj]
        elif isinstance(obj, str):
            if "PVEAPIToken=" in obj:
                obj = re.sub(r"PVEAPIToken=[^=\s]+=[^\s]+", "PVEAPIToken=***REDACTED***", obj)
            # Scrub secret-bearing flags leaked inside joined command strings
            # (Proxmox-cli used to embed the raw password in its `command`
            # field). Shared pattern, so a new flag cannot leak here while
            # being redacted elsewhere.
            obj = redact_command_secrets(obj)
            return obj
        return obj

    def log(self, event_type: str, data: Dict[str, Any]) -> None:
        """Append structured event to JSONL log."""
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "session_id": self.session_id,
            "event": event_type,
            "data": self._redact(data),
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            sys.stderr.write(f"{RED}[Logger Error]{RESET} Could not write log: {e}\n")

    def new_session(self) -> str:
        """Rotate to a fresh session log file and reset counters.

        Returns the previous session id. Logs a SYSTEM boundary event in
        both the old file (session_end) and the new file (session_start)
        so the audit trail stays continuous across /clear and /new.
        """
        old_id = self.session_id
        try:
            self.log("SYSTEM", {"action": "session_end", "next": "rotated"})
        except Exception:
            pass
        self.session_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"chat-{self.session_id}.jsonl"
        self.stats = {
            "start_time": datetime.datetime.now().isoformat(),
            "user_prompts": 0,
            "tool_calls": 0,
            "tool_approvals": 0,
            "tool_denials": 0,
            "errors": 0,
        }
        try:
            self.log("SYSTEM", {"action": "session_start", "previous": old_id})
        except Exception:
            pass
        return old_id

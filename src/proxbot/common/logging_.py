"""JSONL structured logger and log-file discovery."""

import datetime
import json
import pathlib
from typing import Any

from .config import Config
from .constants import DEFAULT_LOG_DIR, REPO_ROOT
from .redact import _redact_argv, redact_string


class Logger:
    """JSONL logger for CLI actions."""

    def __init__(self, log_dir: pathlib.Path, no_log: bool = False, config: Config | None = None):
        self.no_log = no_log
        self.log_dir = log_dir
        self.config = config
        self.entries: list[dict[str, Any]] = []
        self.file_path: pathlib.Path | None = None
        if not no_log:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.file_path = log_dir / f"proxmox-cli-{stamp}.jsonl"
            try:
                self.file_path.touch(exist_ok=True)
            except OSError:
                pass

    def log(
        self,
        event: str,
        argv: list[str],
        method: str | None = None,
        endpoint: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        ok: bool = True,
        dry_run: bool = False,
        response_summary: str | None = None,
        error: str | None = None,
        task_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.datetime.now().astimezone().isoformat(),
            "event": event,
            "argv": argv,
            "dry_run": dry_run,
            "ok": ok,
        }
        if method is not None:
            entry["method"] = method
        if endpoint is not None:
            entry["endpoint"] = endpoint
        if status_code is not None:
            entry["status_code"] = status_code
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if response_summary is not None:
            entry["response_summary"] = response_summary
        if error is not None:
            entry["error"] = error
        if task_id is not None:
            entry["task_id"] = task_id
        if extra:
            entry.update(extra)
        self.entries.append(entry)
        self._flush_entry(entry)

    def _flush_entry(self, entry: dict[str, Any]) -> None:
        if self.no_log or self.file_path is None:
            return
        try:
            flush_entry = dict(entry)
            if self.config is not None:
                flush_entry["argv"] = _redact_argv(flush_entry.get("argv", []), self.config)
                for key, value in flush_entry.items():
                    if isinstance(value, str):
                        flush_entry[key] = redact_string(value, self.config)
            with self.file_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(flush_entry, default=str) + "\n")
        except OSError:
            pass

    def latest_path(self) -> pathlib.Path:
        return self.log_dir / "latest.jsonl"

    def symlink_latest(self) -> None:
        if self.no_log or self.file_path is None:
            return
        latest = self.latest_path()
        try:
            if latest.exists() or latest.is_symlink():
                latest.unlink()
            latest.symlink_to(self.file_path.name)
        except OSError:
            pass


def latest_log_file(log_dir: pathlib.Path) -> pathlib.Path | None:
    """Return the most recently created non-empty log file, or None."""
    if not log_dir.exists():
        return None
    # Prefer the stable `latest.jsonl` symlink if it points to an existing, non-empty file.
    # The symlink is updated after each command, so it reflects the previous command's log.
    # Commands such as `logs tail` create an empty log file; skipping empty files avoids
    # returning the log of the current/last `logs tail` command itself.
    latest_link = log_dir / "latest.jsonl"
    if latest_link.is_symlink():
        target = latest_link.readlink()
        if not target.is_absolute():
            target = log_dir / target
        if target.exists() and target.stat().st_size > 0:
            return target
    files = [
        p for p in log_dir.iterdir()
        if p.is_file() and p.name.startswith("proxmox-cli-") and p.stat().st_size > 0
    ]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)

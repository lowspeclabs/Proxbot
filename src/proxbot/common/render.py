"""Output rendering (json/table/markdown) and stdout/stderr emission."""

import argparse
import json
import pathlib
import sys
from typing import Any

from .constants import REDACTED
from .redact import _redact_command_str


def render_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, default=str)


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(no data)"
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    lines: list[str] = []
    lines.append("  ".join(c.upper().ljust(widths[c]) for c in columns))
    for row in rows:
        lines.append("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
    return "\n".join(lines)


def render_markdown(title: str, items: list[tuple[str, Any]]) -> str:
    lines = [f"## {title}", ""]
    for label, value in items:
        if value is None:
            value = ""
        lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def render_error_markdown(error_type: str, message: str, hint: str, log_path: pathlib.Path | None) -> str:
    lines = ["## Error", "", f"- Type: {error_type}", f"- Message: {message}"]
    if hint:
        lines.append(f"- Hint: {hint}")
    if log_path:
        lines.append(f"- Log: {log_path}")
    return "\n".join(lines)


def requested_format_from_argv(argv: list[str] | None = None) -> str | None:
    """Best-effort format detection for early validation errors."""
    argv = list(sys.argv[1:] if argv is None else argv)
    for idx, arg in enumerate(argv):
        if arg == "--format" and idx + 1 < len(argv):
            return argv[idx + 1]
        if arg.startswith("--format="):
            return arg.partition("=")[2]
    return None


def emit_error(error_type: str, message: str, hint: str, log_path: pathlib.Path | None) -> None:
    result = {
        "ok": False,
        "error_type": error_type,
        "data": None,
        "error": message,
        "hint": hint,
        "log_path": str(log_path) if log_path else None,
    }
    emit_output(result, requested_format_from_argv() or "md", "Error")


def emit_output(result: dict[str, Any], fmt: str, title: str = "Result") -> None:
    normalized = dict(result)
    normalized.setdefault("command", _redact_command_str(" ".join(sys.argv[1:])))
    if "command" in normalized and isinstance(normalized["command"], str):
        normalized["command"] = _redact_command_str(normalized["command"])
    normalized.setdefault("target", None)
    normalized.setdefault("data", None)
    normalized.setdefault("dry_run", bool(isinstance(normalized["data"], dict) and normalized["data"].get("dry_run")))
    normalized.setdefault("task_id", None)
    normalized.setdefault("next_step", "Inspect the result before running another command.")
    normalized.setdefault("log_path", None)
    stream = sys.stderr if not normalized.get("ok", False) else sys.stdout
    print(render_output(normalized, fmt, title), file=stream)


def filter_rows(data: Any, args: argparse.Namespace) -> Any:
    """Apply bounded, predictable filters to resource list responses."""
    if not isinstance(data, list):
        return data
    rows = data
    status = getattr(args, "status", None)
    if status:
        rows = [row for row in rows if isinstance(row, dict) and str(row.get("status", "")).lower() == status.lower()]
    limit = getattr(args, "limit", None)
    if limit is not None:
        rows = rows[:limit]
    return rows

def render_output(result: dict[str, Any], fmt: str, title: str = "Result") -> str:
    ok = result.get("ok", False)
    data = result.get("data")
    error = result.get("error")

    if fmt == "json":
        return render_json(result)

    if not ok:
        return render_error_markdown(
            error_type=result.get("error_type", "api_error"),
            message=str(error or "Unknown error"),
            hint=result.get("hint", ""),
            log_path=result.get("log_path"),
        )

    if fmt == "table":
        if isinstance(data, list) and data:
            return render_table(data, list(data[0].keys()))
        return json.dumps(data, indent=2, default=str)

    # markdown
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, list):
        items = [(str(i), json.dumps(v, default=str)) for i, v in enumerate(data)]
    else:
        items = [("Result", data)]
    return render_markdown(title, items)

"""Request plans, dry-run emission, and destructive safety guards."""

import argparse
import pathlib
import sys
from typing import Any

from .constants import REDACTED, EXIT_SUCCESS
from .logging_ import Logger
from .redact import _redact_command_str, redact_data
from .render import emit_output


def request_plan(method: str, endpoint: str, body: dict[str, Any] | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render an executable request before a write safety guard allows it."""
    plan: dict[str, Any] = {"method": method.upper(), "endpoint": endpoint}
    if target:
        plan["target"] = target
    if body is not None:
        plan["body"] = redact_data(body)
    return plan


def emit_dry_run(args: argparse.Namespace, logger: Logger, plan: dict[str, Any], title: str = "Dry Run") -> int:
    """Emit a validated request plan for a state-changing command."""
    logger.log(
        event="safety_guard",
        argv=sys.argv[1:],
        method=plan["method"],
        endpoint=plan["endpoint"],
        ok=True,
        dry_run=True,
        response_summary="Dry run only. Re-run with --execute to apply.",
        extra={"plan": plan},
    )
    emit_output({
        "ok": True,
        "command": _redact_command_str(" ".join(sys.argv[1:])),
        "target": plan.get("target"),
        "data": plan,
        "dry_run": True,
        "task_id": None,
        "next_step": "Re-run the same command with --execute to apply.",
        "log_path": str(logger.file_path) if logger.file_path else None,
    }, args.format, title=title)
    return EXIT_SUCCESS


def require_execute(args: argparse.Namespace, action: str) -> dict[str, Any] | None:
    """Return a dry-run result if --execute is not present."""
    if getattr(args, "execute", False):
        return None
    return {
        "ok": True,
        "dry_run": True,
        "would_call": action,
        "message": "Dry run only. Re-run with --execute to apply.",
    }


def require_destructive(args: argparse.Namespace, confirm_id: str) -> dict[str, Any] | None:
    """Return a safety-blocked result if destructive guards are missing."""
    if not getattr(args, "execute", False):
        return {
            "ok": True,
            "dry_run": True,
            "would_call": f"destructive action on {confirm_id}",
            "message": "Dry run only. Re-run with --execute to apply.",
        }
    if not getattr(args, "force", False):
        return {
            "ok": False,
            "error_type": "safety_guard",
            "error": "Destructive action requires --force.",
            "hint": "Add --force if you really intend to destroy this resource.",
        }
    if getattr(args, "confirm", None) != confirm_id:
        return {
            "ok": False,
            "error_type": "safety_guard",
            "error": f"Destructive action requires --confirm {confirm_id}.",
            "hint": "Pass the exact resource ID to confirm the operation.",
        }
    return None

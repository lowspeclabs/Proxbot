"""Interactive approval gate for write and destructive tool calls."""

from typing import Any, Dict, Tuple

from .markdown import BOLD, GRAY, GREEN, RED, YELLOW, RESET, prompt_input
from .spec import DESTRUCTIVE_TOOLS, WRITE_TOOLS

class SafetyGate:
    """Manages approval workflow for state-changing and destructive actions."""

    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve

    def check_and_prompt(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, bool]:
        """Prompt operator for approval. Returns (approved, auto_approved_toggled)."""
        node = args.get("node", "N/A")
        vmid = args.get("vmid", "Auto")
        name = args.get("name") or args.get("hostname", "")

        # Destructive Actions
        if tool_name in DESTRUCTIVE_TOOLS:
            print()
            print(f"{RED}{BOLD}! Destructive action: {tool_name}{RESET} {GRAY}— irreversible{RESET}")
            print(f"  {GRAY}node{RESET} {node}  {GRAY}vmid{RESET} {vmid}" + (f"  {GRAY}name{RESET} {name}" if name else ""))

            try:
                ans = prompt_input(f"  Confirm? [yes/no]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}Aborted.{RESET}")
                return False, False

            if ans in {"yes", "y"}:
                return True, False
            else:
                print(f"{GRAY}Rejected.{RESET}")
                return False, False

        # Write Actions (Creation, Start, Stop, Reboot, Snapshot)
        elif tool_name in WRITE_TOOLS:
            if self.auto_approve:
                print(f"{GRAY}· auto-approved {tool_name} ({node}/{name or vmid}){RESET}")
                return True, False

            details = []
            if node and node != "N/A":
                details.append(f"{node}")
            if vmid and vmid != "Auto":
                details.append(f"{vmid}")
            if name and str(name) not in details:
                details.append(f"{name}")
            if args.get("command"):
                details.append(f"`{args.get('command')}`")
            for extra_key in ("template", "storage", "url", "filename"):
                if args.get(extra_key) and str(args[extra_key]) not in details:
                    details.append(f"{args[extra_key]}")
            summary = " · ".join(details)

            print()
            print(f"  {YELLOW}Approve:{RESET} {BOLD}{tool_name}{RESET}" + (f" {GRAY}— {summary}{RESET}" if summary else ""))

            try:
                ans = prompt_input(f"  [y/n/a]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}Aborted.{RESET}")
                return False, False

            if ans in {"a", "auto", "all"}:
                self.auto_approve = True
                print(f"{GREEN}✓ Auto-approval on for this session.{RESET}")
                return True, True
            elif ans in {"y", "yes", ""}:
                return True, False
            else:
                print(f"{GRAY}Rejected.{RESET}")
                return False, False

        # Read-only actions
        return True, False

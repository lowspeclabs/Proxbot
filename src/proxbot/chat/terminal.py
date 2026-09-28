"""Banner, help, readline history, and screen helpers."""

import os
import pathlib
import readline
import sys
from typing import Dict

HISTORY_FILE = pathlib.Path(__file__).resolve().parents[3] / ".chat_history"

from .markdown import BOLD, CYAN, DIM, GRAY, GREEN, RED, RESET, YELLOW, strip_ansi

def clear_screen() -> None:
    """Clear the terminal display including scrollback."""
    try:
        sys.stdout.write("\033[2J\033[H\033[3J")
        sys.stdout.flush()
    except Exception:
        pass


def print_banner(config_info: Dict[str, str]) -> None:
    """Render startup banner and connection info."""
    auto = str(config_info.get("auto_approve", "False")).lower() in {"1", "true", "yes", "on"}
    auto_str = f"{YELLOW}on{RESET}" if auto else f"{GRAY}off{RESET}"
    print(f"\n{BOLD}{CYAN}ProxBot{RESET} {GRAY}— Proxmox AI Assistant{RESET}")
    print(f"  {GRAY}host{RESET}  {config_info.get('pve_url', 'Not Configured')}")
    print(f"  {GRAY}model{RESET} {GREEN}{config_info.get('llm_model', 'Default')}{RESET} {GRAY}via {config_info.get('llm_url', 'Not Configured')} · auto-approve {auto_str}{RESET}")
    print(f"  {GRAY}Type /help for commands, /exit to quit.{RESET}\n")


def print_help() -> None:
    """Print available interactive commands and usage examples."""
    print(f"\n{BOLD}Commands{RESET}")
    cmds = [
        ("/doctor", "Cluster connectivity and config health"),
        ("/nodes", "Cluster nodes summary"),
        ("/vms [node]", "List VMs (all nodes if omitted)"),
        ("/lxcs [node]", "List containers (all nodes if omitted)"),
        ("/templates [node]", "VM, LXC, and OS templates"),
        ("/registry [search]", "Browse upstream LXC template registry"),
        ("/storage [node]", "Storage pools and usage"),
        ("/model [name]", "View or switch LLM model"),
        ("/auto", "Toggle auto-approval for writes"),
        ("/new", "New session: reset context, keep screen"),
        ("/clear", "Clear screen + reset context (fresh log)"),
        ("/exit", "Session stats and quit"),
    ]
    for c, desc in cmds:
        print(f"  {CYAN}{c:<16}{RESET} {GRAY}{desc}{RESET}")
    print(f"\n{BOLD}Examples{RESET}")
    print(f"  {GRAY}“What VMs are running?” · “Create an LXC called demo-1” · “Show storage”{RESET}\n")


def setup_readline() -> None:
    """Configure readline history for terminal arrow key navigation."""
    try:
        readline.parse_and_bind("tab: complete")
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
    except Exception:
        pass


def save_readline() -> None:
    """Save readline history to file on exit."""
    try:
        readline.set_history_length(1000)
        readline.write_history_file(str(HISTORY_FILE))
    except Exception:
        pass

#!/usr/bin/env python3
"""
chat.py — Interactive AI Assistant Chat Interface for Proxmox VE.

Translates natural language prompts into Proxmox management operations via the
safety-guarded Proxmox-cli.py CLI wrapper.

This module is the composition root: the implementation lives in the `proxmbot`
package (src/proxbot/), and the names are re-exported here so existing callers,
tests (`import chat`), and the approval/REPL loop keep working unchanged.
"""

import argparse
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

_SRC = pathlib.Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from proxbot.chat.markdown import (  # noqa: F401
    RESET, BOLD, DIM, ITALIC, UNDERLINE,
    RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, GRAY,
    BG_RED, BG_GREEN, BG_YELLOW, BG_BLUE, BG_MAGENTA, BG_CYAN, BG_DARK,
    ANSI_ESCAPE_RE, STRIP_ANSI_RE,
    strip_ansi, readline_prompt, prompt_input, render_markdown,
)
from proxbot.chat.session import SessionLogger  # noqa: F401
from proxbot.chat.streamer import RollingThoughtStreamer  # noqa: F401
from proxbot.chat.spec import (  # noqa: F401
    TOOLS_SPEC, READ_ONLY_TOOLS, WRITE_TOOLS, DESTRUCTIVE_TOOLS,
)
from proxbot.chat.watchdog import LoopWatchdog, extract_embedded_tool_calls  # noqa: F401
from proxbot.chat.executor import ProxmoxToolExecutor  # noqa: F401
from proxbot.chat.safety import SafetyGate  # noqa: F401
from proxbot.chat.agent import ChatAgent  # noqa: F401
from proxbot.chat.terminal import (  # noqa: F401
    clear_screen, print_banner, print_help, setup_readline, save_readline,
)
from proxbot.chat import models as _models
from proxbot.chat.models import fetch_backend_models, resolve_model  # noqa: F401
from proxbot.chat.models import requests  # noqa: F401  (patched by tests)

REPO_ROOT = pathlib.Path(__file__).resolve().parent
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_CHAT_LOG_DIR = REPO_ROOT / "scripts" / "logs" / "chat-sessions"
PROXMOX_CLI_PATH = REPO_ROOT / "scripts" / "Proxmox-cli.py"
HISTORY_FILE = REPO_ROOT / ".chat_history"


def load_env_file(path: pathlib.Path) -> Dict[str, str]:
    """Parse KEY=VALUE .env file, ignoring comments and blanks."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chat.py",
        description="Autonomous interactive AI assistant chat for Proxmox VE."
    )
    parser.add_argument("--env-file", default=str(ENV_PATH), help="Path to .env configuration file")
    parser.add_argument("--base-url", help="Override LLM API base URL")
    parser.add_argument("--api-key", help="Override LLM API key")
    parser.add_argument("--model", help="Pin a specific LLM model (default: use the model already loaded on the backend; 'auto' forces detection)")
    parser.add_argument("--temperature", type=float, help="Override LLM sampling temperature")
    parser.add_argument("--auto-approve", action="store_true", help="Start with auto-approval enabled")
    parser.add_argument("prompt", nargs="*", help="Optional single prompt to execute non-interactively")

    args = parser.parse_args(argv)

    env_file_path = pathlib.Path(args.env_file).expanduser().resolve()
    env = load_env_file(env_file_path)

    base_url = args.base_url or env.get("LLM_BASE_URL", "http://192.168.1.9:1234/v1")
    api_key = args.api_key or env.get("LLM_API_KEY", "not-needed")
    # Default: use whatever model the backend already has loaded so we don't
    # force it to load something else. --model (or LLM_MODEL as fallback)
    # only matters when explicitly pinned or when detection fails.
    fallback_model = env.get("LLM_MODEL", "qwen3.5-4b") or "qwen3.5-4b"
    model, model_notice = resolve_model(base_url, api_key, args.model, fallback_model)
    if model_notice:
        print(f"{YELLOW}! {model_notice}{RESET}")
    elif not args.model and model != fallback_model:
        print(f"{GRAY}Detected backend model: {model}{RESET}")
    temperature = args.temperature if args.temperature is not None else float(env.get("LLM_TEMPERATURE", "0.2"))
    max_turns = int(env.get("LLM_MAX_TURNS", "15"))
    auto_approve = args.auto_approve or (env.get("CHAT_AUTO_APPROVE", "false").lower() in {"1", "true", "yes"})

    log_dir_raw = env.get("CHAT_LOG_DIR", "scripts/logs/chat-sessions")
    log_dir = pathlib.Path(log_dir_raw)
    if not log_dir.is_absolute():
        log_dir = REPO_ROOT / log_dir

    pve_url = env.get("PROXMOX_API_URL", "Configured in .env")

    logger = SessionLogger(log_dir)
    executor = ProxmoxToolExecutor(PROXMOX_CLI_PATH, env_file_path)
    safety_gate = SafetyGate(auto_approve=auto_approve)
    agent = ChatAgent(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_turns=max_turns,
        executor=executor,
        safety_gate=safety_gate,
        logger=logger,
    )

    if args.prompt:
        prompt_text = " ".join(args.prompt).strip()
        response = agent.chat(prompt_text)
        print(render_markdown(response))
        return 0

    setup_readline()
    print_banner({
        "pve_url": pve_url,
        "llm_url": base_url,
        "llm_model": model,
        "auto_approve": str(safety_gate.auto_approve),
    })

    try:
        while True:
            try:
                user_line = prompt_input("proxbot › ").strip()
            except KeyboardInterrupt:
                print(f"\n{GRAY}(Use /exit or Ctrl+D to quit){RESET}")
                continue
            except EOFError:
                print()
                break

            if not user_line:
                continue

            if user_line.startswith("/"):
                cmd_parts = user_line.split()
                cmd = cmd_parts[0].lower()
                cmd_arg = cmd_parts[1] if len(cmd_parts) > 1 else None

                if cmd in {"/exit", "/quit", "/q"}:
                    break

                elif cmd == "/help":
                    print_help()
                    continue

                elif cmd == "/clear":
                    agent.clear_history()
                    logger.new_session()
                    clear_screen()
                    print_banner({
                        "pve_url": pve_url,
                        "llm_url": base_url,
                        "llm_model": agent.model,
                        "auto_approve": str(safety_gate.auto_approve),
                    })
                    print(f"{GRAY}Context cleared — fresh session {logger.session_id}.{RESET}\n")
                    continue

                elif cmd == "/new":
                    agent.clear_history()
                    logger.new_session()
                    print(f"{GRAY}— new session {logger.session_id} (history kept on screen, context reset){RESET}\n")
                    continue

                elif cmd == "/auto":
                    safety_gate.auto_approve = not safety_gate.auto_approve
                    state = "on" if safety_gate.auto_approve else "off"
                    print(f"{GRAY}Auto-approve {state}.{RESET}\n")
                    continue

                elif cmd == "/model":
                    if cmd_arg:
                        agent.model = cmd_arg
                        print(f"{GRAY}Model: {RESET}{cmd_arg}\n")
                    else:
                        print(f"{GRAY}Model: {RESET}{agent.model}")
                        backend_models = fetch_backend_models(agent.base_url, agent.api_key)
                        if backend_models:
                            print(f"{GRAY}Backend:{RESET}")
                            for m in backend_models:
                                marker = f"{GREEN}●{RESET}" if m == agent.model else f"{GRAY}○{RESET}"
                                print(f"  {marker} {m}")
                            print()
                        else:
                            print(f"{GRAY}(backend model list unreachable){RESET}\n")
                    continue

                elif cmd == "/doctor":
                    print(f"{GRAY}Checking cluster health…{RESET}")
                    res = executor.execute("doctor", {"live": True})
                    print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    continue

                elif cmd == "/nodes":
                    res = executor.execute("nodes_list", {})
                    data = res.get("data", [])
                    if isinstance(data, list) and data:
                        tbl = "| Node | Status | CPU Usage | Memory Usage | Uptime |\n|---|---|---|---|---|\n"
                        for n in data:
                            cpu = f"{n.get('cpu', 0)*100:.1f}%" if 'cpu' in n else "N/A"
                            mem = f"{n.get('mem', 0)/(1024**3):.1f} / {n.get('maxmem', 0)/(1024**3):.1f} GB" if 'mem' in n else "N/A"
                            uptime = f"{n.get('uptime', 0)//3600}h" if 'uptime' in n else "N/A"
                            tbl += f"| {n.get('node', 'N/A')} | {n.get('status', 'N/A')} | {cpu} | {mem} | {uptime} |\n"
                        print(render_markdown(tbl))
                    else:
                        print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    continue

                elif cmd == "/vms":
                    res = executor.execute("vms_list", {"node": cmd_arg} if cmd_arg else {})
                    data = res.get("data", [])
                    if isinstance(data, list) and data:
                        tbl = "| VMID | Name | Node | Status | CPU | Memory | Uptime |\n|---|---|---|---|---|---|---|\n"
                        for v in data:
                            is_tmpl = bool(v.get("template") == 1 or v.get("template") is True)
                            st_display = "template" if is_tmpl else v.get("status", "N/A")
                            cpu = f"{v.get('cpu', 0)*100:.1f}%" if 'cpu' in v else "N/A"
                            mem = f"{v.get('mem', 0)/(1024**2):.0f} / {v.get('maxmem', 0)/(1024**2):.0f} MB" if 'mem' in v else "N/A"
                            uptime = f"{v.get('uptime', 0)//3600}h" if 'uptime' in v else "N/A"
                            tbl += f"| {v.get('vmid', 'N/A')} | {v.get('name', 'N/A')} | {v.get('node', 'N/A')} | {st_display} | {cpu} | {mem} | {uptime} |\n"
                        print(render_markdown(tbl))
                    else:
                        print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    continue

                elif cmd == "/lxcs":
                    res = executor.execute("lxcs_list", {"node": cmd_arg} if cmd_arg else {})
                    data = res.get("data", [])
                    if isinstance(data, list) and data:
                        tbl = "| VMID | Name | Node | Status | CPU | Memory | Uptime |\n|---|---|---|---|---|---|---|\n"
                        for c in data:
                            is_tmpl = bool(c.get("template") == 1 or c.get("template") is True)
                            st_display = "template" if is_tmpl else c.get("status", "N/A")
                            cpu = f"{c.get('cpu', 0)*100:.1f}%" if 'cpu' in c else "N/A"
                            mem = f"{c.get('mem', 0)/(1024**2):.0f} / {c.get('maxmem', 0)/(1024**2):.0f} MB" if 'mem' in c else "N/A"
                            uptime = f"{c.get('uptime', 0)//3600}h" if 'uptime' in c else "N/A"
                            tbl += f"| {c.get('vmid', 'N/A')} | {c.get('name', 'N/A')} | {c.get('node', 'N/A')} | {st_display} | {cpu} | {mem} | {uptime} |\n"
                        print(render_markdown(tbl))
                    else:
                        print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    continue

                elif cmd == "/storage":
                    res = executor.execute("storage_list", {"node": cmd_arg} if cmd_arg else {})
                    data = res.get("data", [])
                    if isinstance(data, list) and data:
                        tbl = "| Storage | Node | Type | Status | Avail | Total | Content Types |\n|---|---|---|---|---|---|---|\n"
                        for s in data:
                            total_gb = f"{s.get('total', 0)/(1024**3):.1f} GB" if s.get('total') else "N/A"
                            avail_gb = f"{s.get('avail', 0)/(1024**3):.1f} GB" if s.get('avail') else "N/A"
                            st_active = "active" if s.get('active') else "inactive"
                            tbl += f"| {s.get('storage', 'N/A')} | {s.get('node', 'N/A')} | {s.get('type', 'N/A')} | {st_active} | {avail_gb} | {total_gb} | {s.get('content', 'N/A')} |\n"
                        print(render_markdown(tbl))
                elif cmd == "/templates":
                    res = executor.execute("templates_list", {"node": cmd_arg} if cmd_arg else {})
                    data = res.get("data", {})
                    vm_tmpls = data.get("vm_templates", [])
                    lxc_tmpls = data.get("lxc_templates", [])
                    os_tarballs = data.get("lxc_os_tarballs", [])

                    out_lines = []
                    if vm_tmpls:
                        out_lines.append("\n### Virtual Machine Templates")
                        out_lines.append("| VMID | Template Name | Node | Cores | Memory | Status |\n|---|---|---|---|---|---|")
                        for v in vm_tmpls:
                            mem_mb = f"{v.get('maxmem', 0)/(1024**2):.0f} MB" if v.get('maxmem') else "N/A"
                            out_lines.append(f"| {v.get('vmid', 'N/A')} | {v.get('name', 'N/A')} | {v.get('node', 'N/A')} | {v.get('cpus', 'N/A')} | {mem_mb} | template |")

                    if lxc_tmpls:
                        out_lines.append("\n### Converted LXC Container Templates")
                        out_lines.append("| VMID | Template Name | Node | Cores | Memory | Status |\n|---|---|---|---|---|---|")
                        for c in lxc_tmpls:
                            mem_mb = f"{c.get('maxmem', 0)/(1024**2):.0f} MB" if c.get('maxmem') else "N/A"
                            out_lines.append(f"| {c.get('vmid', 'N/A')} | {c.get('name', 'N/A')} | {c.get('node', 'N/A')} | {c.get('cpus', 'N/A')} | {mem_mb} | template |")

                    if os_tarballs:
                        out_lines.append("\n### LXC Storage OS Tarball Templates")
                        out_lines.append("| Template File / VolID | Node | Size | Format |\n|---|---|---|---|")
                        for o in os_tarballs:
                            size_mb = f"{o.get('size', 0)/(1024**2):.1f} MB" if o.get('size') else "N/A"
                            out_lines.append(f"| {o.get('volid', 'N/A')} | {o.get('node', 'N/A')} | {size_mb} | {o.get('format', 'N/A')} |")

                    if not vm_tmpls and not lxc_tmpls and not os_tarballs:
                        print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    else:
                        print(render_markdown("\n".join(out_lines)))
                    continue

                elif cmd == "/registry":
                    res = executor.execute("templates_registry", {"search": cmd_arg} if cmd_arg else {})
                    data = res.get("data", [])
                    if isinstance(data, list) and data:
                        tbl = "| Template | Section | OS | Version | Headline |\n|---|---|---|---|---|\n"
                        for r in data[:50]:
                            tbl += (f"| {r.get('template', 'N/A')} | {r.get('section', 'N/A')} | "
                                    f"{r.get('os', 'N/A')} | {r.get('version', 'N/A')} | "
                                    f"{str(r.get('headline', 'N/A'))[:60]} |\n")
                        if len(data) > 50:
                            tbl += f"\n_Showing 50 of {len(data)} — refine with a search term._\n"
                        print(render_markdown(tbl))
                    else:
                        print(render_markdown(f"```json\n{json.dumps(res, indent=2)}\n```\n"))
                    continue

                else:
                    print(f"{GRAY}Unknown command {cmd} — try /help.{RESET}\n")
                    continue

            # Standard Chat
            try:
                response = agent.chat(user_line)
                if response:
                    print()
                    print(render_markdown(response))
                    print()
            except KeyboardInterrupt:
                print(f"\n{GRAY}Interrupted.{RESET}\n")

    finally:
        save_readline()
        print(f"\n{GRAY}— session: {logger.stats['user_prompts']} prompts · {logger.stats['tool_calls']} tools "
              f"({logger.stats['tool_approvals']} approved, {logger.stats['tool_denials']} denied) · log {logger.log_file}{RESET}\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())

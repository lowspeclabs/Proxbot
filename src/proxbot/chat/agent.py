"""The LLM conversation loop, streaming, and tool-call orchestration."""

import getpass
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

from ..common.redact import redact_command_secrets
from .markdown import CYAN, GRAY, GREEN, RED, RESET, YELLOW
from .executor import ProxmoxToolExecutor
from .safety import SafetyGate
from .session import SessionLogger
from .spec import READ_ONLY_TOOLS, TOOLS_SPEC
from .streamer import RollingThoughtStreamer
from .watchdog import LoopWatchdog, extract_embedded_tool_calls

class ChatAgent:
    """Autonomous agent orchestrating LLM conversation, tool calls, and execution."""

    SYSTEM_PROMPT = """You are ProxBot, an expert AI infrastructure engineer for Proxmox VE.
You interact with Proxmox through safe, structured tools that interface with `Proxmox-cli.py`.

Capabilities:
1. Inspecting:
   - Nodes: nodes_list, node_status
   - VMs & VM Templates: vms_list, vm_status, vm_config, vm_guest_network
   - LXCs & CT Templates: lxcs_list, lxc_status, lxc_config
   - Templates (VMs, LXCs, OS tarballs): templates_list
   - Storage & Content: storage_list, storage_content, storage_templates
   - Tasks: tasks_recent
2. Creating: You CAN and SHOULD create LXC containers and VMs when requested by the user.
   - Fresh LXC from OS tarball: check free IDs with `lxcs_next_id`, tarballs with `storage_templates` (or `templates_list`), pools with `storage_list` (choose one supporting 'LXC rootfs'), then call `lxc_create` with `ostemplate=<storage>:vztmpl/<file>.tar.*`.
   - New LXC from converted template (template:1 VMID, e.g. 20300): NEVER call `lxc_create` with the VMID as `ostemplate`. Call `lxc_clone` with `vmid=<template-vmid>`, `new_vmid=<free-id>`, `hostname`, `storage`.
   - New VM from VM template: NEVER call `vm_create`. Call `vm_clone` with `vmid=<template-vmid>`, `new_vmid=<free-id>`.
   - When asked to create a VM from scratch: check free IDs with `vms_next_id` and storage with `storage_list` (choose one supporting 'VM disks'), then call `vm_create`.
3. In-Guest Command Execution & Configuration:
   - `vm_exec`: Run shell commands directly inside any running VM via the Proxmox QEMU Guest Agent REST API.
   - `lxc_exec`: Run shell commands directly inside any running LXC container via the Proxmox API (fast, works without SSH or network IP).
   - `ssh_exec`: Run shell commands over SSH on VMs, LXCs, or remote hosts.
4. Managing: start, shutdown, reboot, snapshot, and delete VMs and LXCs.

Templates in Proxmox:
• VM Templates: Full QEMU virtual machines converted to templates (`template: 1` in vms_list). Clone with `vm_clone` to create new VMs.
• Converted LXC Templates: Container guests converted to templates (`template: 1` in lxcs_list). Clone with `lxc_clone` — `lxc_create --ostemplate <vmid>` is invalid and fails with `Only root can pass arbitrary filesystem paths`.
• LXC OS Tarball Templates: Base OS images (.tar.zst/.tar.xz) stored in storage pools (`vztmpl`, e.g. `local:vztmpl/debian-13-....tar.zst`). Build fresh containers with `lxc_create` only from these volids.
• Upstream Registry: Hundreds of downloadable LXC images (`templates_registry`, sections `system`/`turnkeylinux`/`mail`). If the needed OS is not cached locally, browse the registry with `templates_registry`, then fetch it with `templates_download` (approval-gated) before `lxc_create`.
• When the user asks about templates (VM templates, container templates, or all templates), ALWAYS call `templates_list` or check `vms_list`/`lxcs_list` for `template == 1`. Never claim Proxmox does not have VM templates.

Guidelines:
• Always execute the appropriate tool calls directly. The chat interface will automatically handle interactive safety approvals with the user.
• For lxc_exec: call WITHOUT password/user args. If the container needs login, the harness securely prompts via getpass and retries. NEVER ask the user to paste a password into chat (it would persist in logs/history).
• Never call vm_guest_network on an LXC vmid — use lxc_status/lxc_config (LXCs have no qemu-server conf). If a tool fails with a type error, switch to the matching vm_* vs lxc_* tool instead of retrying.
• Run ONE in-guest probe first (e.g. `cat /etc/os-release`) before firing parallel exec batches; exec can block 10-60s per call.
• Present data in clean Markdown tables with column alignments.
• Be direct, concise, and helpful.
"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        max_turns: int,
        executor: ProxmoxToolExecutor,
        safety_gate: SafetyGate,
        logger: SessionLogger,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_turns = max_turns
        self.executor = executor
        self.safety_gate = safety_gate
        self.logger = logger
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.SYSTEM_PROMPT}
        ]
        self.watchdog = LoopWatchdog()

    def clear_history(self) -> None:
        """Reset conversation context while keeping the system prompt."""
        self.messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        self.watchdog = LoopWatchdog()

    def _prune_history(self) -> None:
        """Prune older tool response payloads to prevent context overflow."""
        if len(self.messages) > 16:
            for idx in range(1, len(self.messages) - 8):
                msg = self.messages[idx]
                if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                    if len(msg["content"]) > 1000:
                        msg["content"] = msg["content"][:1000] + "... [Older tool output truncated for context]"

    def _extract_error_detail(self, result: Dict[str, Any]) -> str:
        """Extract a clean, concise error summary string from a tool result."""
        err = result.get("error") or result.get("stderr") or result.get("raw_output") or "Operation failed."
        if isinstance(err, str):
            # Handle concatenated JSON docs (legacy double-emit): use the LAST doc.
            docs = ProxmoxToolExecutor._parse_cli_json_blobs(err) if isinstance(err, str) else []
            if docs:
                last = docs[-1]
                inner = last.get("error") or last.get("message")
                if inner and isinstance(inner, str):
                    return inner.strip().split("\n")[0][:300]
            try:
                parsed = json.loads(err)
                if isinstance(parsed, dict):
                    inner = parsed.get("error") or parsed.get("message")
                    if inner:
                        if isinstance(inner, str) and inner.startswith("{"):
                            try:
                                deep = json.loads(inner)
                                return deep.get("message", inner).strip()
                            except Exception:
                                return inner.strip()
                        return str(inner).strip()
            except Exception:
                pass
            # If stderr leaked a full CLI envelope, surface its error line, not "{".
            for line in str(err).strip().split("\n"):
                s = line.strip()
                if not s or s in ("{", "}"):
                    continue
                # Skip JSON structural lines; prefer the "error": "..." line.
                m = re.search(r'"error"\s*:\s*"([^"]+)"', s)
                if m:
                    return m.group(1).strip()[:300]
            first = str(err).strip().split("\n")[0].strip()
            if first in ("{", "}"):
                return "Operation failed (unparseable CLI output). Check latest log."
            return first[:300]
        return str(err).strip().split("\n")[0][:300]

    def chat(self, user_input: str) -> str:
        """Execute multi-turn agent loop for a user input prompt."""
        if not requests:
            err = "Error: 'requests' package is missing. Please run `pip install requests`."
            print(f"{RED}{err}{RESET}")
            return err

        self.messages.append({"role": "user", "content": user_input})
        self.logger.log("USER", {"content": user_input})
        self.logger.stats["user_prompts"] += 1
        self.watchdog.reset_turn()

        turn = 0
        final_reply = ""
        streamer = None

        try:
            while turn < self.max_turns:
                turn += 1
                self._prune_history()

                streamer = RollingThoughtStreamer()
                streamer.start()

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key or 'not-needed'}",
                }
                payload = {
                    "model": self.model,
                    "messages": self.messages,
                    "tools": TOOLS_SPEC,
                    "temperature": self.temperature,
                    "stream": True,
                }

                accumulated_content = ""
                accumulated_reasoning = ""
                accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}
                in_inline_think = False

                try:
                    url = f"{self.base_url}/chat/completions"
                    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
                    if resp.status_code != 200:
                        streamer.finish()
                        error_msg = f"LLM API Error ({resp.status_code}): {resp.text}"
                        print(f"\n{RED}{error_msg}{RESET}")
                        self.logger.log("ERROR", {"status_code": resp.status_code, "text": resp.text})
                        self.logger.stats["errors"] += 1
                        return error_msg

                    for chunk_line in resp.iter_lines():
                        if not chunk_line:
                            continue
                        line_str = chunk_line.decode("utf-8")
                        if not line_str.startswith("data:"):
                            continue
                        data_part = line_str[5:].strip()
                        if data_part == "[DONE]":
                            break

                        try:
                            chunk_json = json.loads(data_part)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk_json.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})

                        # Capture reasoning tokens
                        reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning_chunk:
                            accumulated_reasoning += reasoning_chunk
                            streamer.push(reasoning_chunk)

                        # Capture content tokens and inline <think> tags
                        content_chunk = delta.get("content")
                        if content_chunk:
                            if "<think>" in content_chunk:
                                in_inline_think = True
                                parts = content_chunk.split("<think>", 1)
                                accumulated_content += parts[0]
                                streamer.push(parts[1])
                                accumulated_reasoning += parts[1]
                            elif "</think>" in content_chunk and in_inline_think:
                                in_inline_think = False
                                parts = content_chunk.split("</think>", 1)
                                streamer.push(parts[0])
                                accumulated_reasoning += parts[0]
                                accumulated_content += parts[1]
                            elif in_inline_think:
                                streamer.push(content_chunk)
                                accumulated_reasoning += content_chunk
                            else:
                                accumulated_content += content_chunk

                        # Capture tool calls deltas
                        tc_deltas = delta.get("tool_calls", [])
                        for tc in tc_deltas:
                            idx = tc.get("index", 0)
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.get("id"):
                                accumulated_tool_calls[idx]["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                accumulated_tool_calls[idx]["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                accumulated_tool_calls[idx]["function"]["arguments"] += fn["arguments"]

                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    streamer.finish()
                    err = f"Failed connecting to LLM endpoint {self.base_url}: {e}"
                    print(f"\n{RED}{err}{RESET}")
                    self.logger.log("ERROR", {"exception": str(e)})
                    self.logger.stats["errors"] += 1
                    return err
                finally:
                    if streamer:
                        streamer.finish()

                # Check if fallback tool call extraction is needed from content
                extracted_tool_calls: List[Dict[str, Any]] = []
                if not accumulated_tool_calls and accumulated_content:
                    cleaned, extracted = extract_embedded_tool_calls(accumulated_content)
                    if extracted:
                        accumulated_content = cleaned
                        extracted_tool_calls = extracted

                tool_calls_list = list(accumulated_tool_calls.values()) if accumulated_tool_calls else extracted_tool_calls

                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": accumulated_content or None,
                }
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list

                self.messages.append(assistant_msg)
                self.logger.log("AGENT", {
                    "content": accumulated_content,
                    "reasoning": accumulated_reasoning,
                    "tool_calls": tool_calls_list
                })

                if not tool_calls_list:
                    final_reply = accumulated_content
                    break

                for tc in tool_calls_list:
                    call_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")

                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args if isinstance(raw_args, dict) else {})
                    except json.JSONDecodeError:
                        args = {}

                    args_preview = ", ".join(f"{k}={v}" for k, v in args.items())
                    call_line = f"{CYAN}→ {name}{RESET}" + (f" {GRAY}({args_preview}){RESET}" if args_preview else "")
                    print(call_line)

                    self.logger.log("TOOL_CALL", {"tool": name, "arguments": args, "call_id": call_id})
                    self.logger.stats["tool_calls"] += 1

                    allowed, suppression_reason = self.watchdog.check_call(name, args)
                    if not allowed:
                        print(f"{YELLOW}! {suppression_reason}{RESET}")
                        tool_result_content = json.dumps({"ok": False, "error": suppression_reason})
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": tool_result_content,
                        })
                        continue

                    approved, auto_toggled = self.safety_gate.check_and_prompt(name, args)
                    if not approved:
                        self.logger.log("TOOL_DENIED", {"tool": name, "arguments": args})
                        self.logger.stats["tool_denials"] += 1
                        tool_result_content = json.dumps({
                            "ok": False,
                            "status": "denied",
                            "error": "The user declined approval for this operation."
                        })
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": tool_result_content,
                        })
                        continue

                    self.logger.stats["tool_approvals"] += 1

                    # Progress heartbeat: lxc/vm exec can block 10-60s (SSH probe +
                    # termproxy login). Print now so the UI doesn't look stuck.
                    if name in ("lxc_exec", "vm_exec", "ssh_exec"):
                        print(f"  {GRAY}running… (up to {args.get('timeout', 30)}s){RESET}", flush=True)

                    t0 = time.time()
                    result = self.executor.execute(name, args)
                    duration = time.time() - t0

                    def _is_login_required(res: Dict[str, Any]) -> bool:
                        if res.get("status") == "login_required":
                            return True
                        data = res.get("data")
                        if isinstance(data, dict) and data.get("status") == "login_required":
                            return True
                        # Legacy: CLI envelope buried inside error/raw_output string
                        for key in ("error", "stderr", "raw_output"):
                            val = res.get(key)
                            if isinstance(val, str) and "login_required" in val:
                                return True
                        return False

                    # Interactive Password Escalation for LXC container login
                    if name == "lxc_exec" and _is_login_required(result):
                        target_node = args.get("node", "N/A")
                        target_vmid = args.get("vmid", "N/A")
                        # executor.py only emits --user when the model set it, so
                        # an absent/empty value resolves to the CLI default (root).
                        target_user = args.get("user") or "root"
                        print(f"\n  {YELLOW}Login required for {target_vmid} on {target_node}.{RESET}")
                        try:
                            pwd = getpass.getpass(f"  Password for {target_user}@{target_node}:{target_vmid} (Enter to cancel): ")
                        except (EOFError, KeyboardInterrupt):
                            pwd = ""

                        if pwd:
                            print(f"  {GRAY}Authenticating…{RESET}")
                            auth_args = dict(args)
                            auth_args["password"] = pwd
                            t0 = time.time()
                            result = self.executor.execute(name, auth_args)
                            duration = time.time() - t0

                    is_ok = result.get("ok", False)
                    if is_ok:
                        print(f"  {GREEN}✓{RESET} {GRAY}{duration:.1f}s{RESET}")
                    else:
                        err_msg = self._extract_error_detail(result)
                        print(f"  {RED}✗ {err_msg}{RESET} {GRAY}({duration:.1f}s){RESET}")

                    # Sanitize result to ensure password never enters logs or messages.
                    # _redact() already runs inside logger.log, but the tool message
                    # sent back to the LLM must also be scrubbed (CLI `command`
                    # field historically embedded `--password <secret>`).
                    sanitized_result = json.loads(json.dumps(result, ensure_ascii=False))
                    if isinstance(sanitized_result, dict):
                        if "password" in sanitized_result:
                            sanitized_result["password"] = "***REDACTED***"
                        if isinstance(sanitized_result.get("data"), dict) and "password" in sanitized_result["data"]:
                            sanitized_result["data"]["password"] = "***REDACTED***"
                        if isinstance(sanitized_result.get("command"), str):
                            sanitized_result["command"] = redact_command_secrets(
                                sanitized_result["command"]
                            )
                        if isinstance(sanitized_result.get("error"), str):
                            sanitized_result["error"] = redact_command_secrets(
                                sanitized_result["error"]
                            )

                    self.logger.log("TOOL_RESULT", {"tool": name, "result": sanitized_result, "duration_s": duration})

                    tool_result_content = json.dumps(sanitized_result, ensure_ascii=False)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": tool_result_content,
                    })

        except KeyboardInterrupt:
            if streamer:
                streamer.finish()
            print(f"\n{YELLOW}[Interrupted by user]{RESET}\n")
            self.logger.log("USER_INTERRUPT", {"prompt": user_input})
            return ""

        return final_reply

"""Loop suppression and embedded tool-call extraction."""

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

def extract_embedded_tool_calls(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Extract tool calls embedded directly into content (XML tags or markdown JSON)."""
    tool_calls: List[Dict[str, Any]] = []
    cleaned_text = text

    # Pattern 1: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    xml_pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
    for match in xml_pattern.finditer(text):
        raw_json = match.group(1)
        try:
            parsed = json.loads(raw_json)
            name = parsed.get("name") or parsed.get("tool")
            args = parsed.get("arguments") or parsed.get("parameters") or {}
            if name:
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args)
                    }
                })
        except Exception:
            pass
    cleaned_text = xml_pattern.sub("", cleaned_text).strip()

    # Pattern 2: ```json {"name": "...", "arguments": {...}} ```
    json_block_pattern = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.DOTALL)
    for match in json_block_pattern.finditer(cleaned_text):
        raw_json = match.group(1)
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and ("name" in parsed or "tool" in parsed):
                name = parsed.get("name") or parsed.get("tool")
                args = parsed.get("arguments") or parsed.get("parameters") or {}
                if name:
                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else str(args)
                        }
                    })
                    cleaned_text = cleaned_text.replace(match.group(0), "").strip()
        except Exception:
            pass

    return cleaned_text, tool_calls


# ---------------------------------------------------------------------------
# Loop & Duplicate Watchdog
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Harness-assembled command validation
# ---------------------------------------------------------------------------
def validate_harness_command(tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Refuse a harness-assembled tool call the harness did not itself construct.

    This is the load-bearing safety check. It runs in the watchdog, before the
    approval prompt, so it cannot be satisfied by typing `y` or by
    --auto-approve. The model supplies only `vmid` and `command`; anything that
    tries to influence the node, the `pct` invocation, or the shell is rejected
    outright rather than sanitised, because a partially-honoured injection
    attempt is exactly the case worth stopping.
    """
    if tool_name != "lxc_pct_exec":
        return True, None

    extra = sorted(set(arguments) - {"node", "vmid", "command", "timeout"})
    if extra:
        return False, (
            f"lxc_pct_exec does not accept {', '.join(extra)}. The node, the pct invocation and "
            f"the shell are fixed by the harness. Use the API tools to act on the node itself."
        )

    vmid = arguments.get("vmid")
    if isinstance(vmid, bool) or not isinstance(vmid, (int, str)):
        return False, f"lxc_pct_exec requires an integer vmid, got {type(vmid).__name__}."
    if not str(vmid).strip().isdigit():
        return False, (
            f"lxc_pct_exec requires a numeric vmid, got {vmid!r}. The vmid is interpolated into "
            f"the node command line, so it must be digits only."
        )

    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return False, "lxc_pct_exec requires a non-empty string command."

    node = arguments.get("node")
    if node is not None and (not isinstance(node, str) or not node.strip()):
        return False, "lxc_pct_exec node must be a non-empty string when supplied."

    timeout = arguments.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))):
        return False, f"lxc_pct_exec timeout must be numeric, got {type(timeout).__name__}."

    # Not a security boundary: the command is a single quoted argument to
    # /bin/sh -lc on the node, so a nested `pct` would execute inside the
    # container where it does not exist. This only catches the model treating
    # this tool as a node shell, which would otherwise produce a confusing
    # "command not found" instead of a clear refusal.
    if re.match(r"\s*(?:[\w./-]*/)?pct\b", command):
        return False, (
            "lxc_pct_exec refuses a command that starts with `pct`. This tool runs the command "
            "inside the container, not on the node; pct does not exist in the guest. Use the "
            "Proxmox API tools for node-level operations."
        )

    return True, None


class LoopWatchdog:
    """Detects and prevents duplicate or infinite tool execution loops."""

    def __init__(self, max_turn_calls: int = 12, max_duplicate_calls: int = 2):
        self.max_turn_calls = max_turn_calls
        self.max_duplicate_calls = max_duplicate_calls
        self.history: List[Tuple[str, str]] = []
        self.call_counts: Dict[Tuple[str, str], int] = {}
        self.total_calls_in_turn = 0

    def reset_turn(self) -> None:
        self.total_calls_in_turn = 0

    def check_call(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Return (allowed, reason_if_suppressed)."""
        # Harness-assembled tools are validated first and unconditionally: a
        # rejected command must never reach the approval prompt.
        allowed, reason = validate_harness_command(tool_name, arguments)
        if not allowed:
            return False, reason

        self.total_calls_in_turn += 1
        if self.total_calls_in_turn > self.max_turn_calls:
            return False, f"Maximum tool executions per turn ({self.max_turn_calls}) exceeded. Stopping loop."

        arg_sig = json.dumps(arguments, sort_keys=True)
        sig = (tool_name, arg_sig)

        count = self.call_counts.get(sig, 0) + 1
        self.call_counts[sig] = count
        self.history.append(sig)

        if count > self.max_duplicate_calls:
            return False, f"Duplicate tool call suppressed: '{tool_name}' with arguments {arg_sig} has already been executed {count-1} times. Analyze the prior result."

        if len(self.history) >= 4:
            if self.history[-1] == self.history[-3] and self.history[-2] == self.history[-4]:
                return False, "Cyclic alternating tool call sequence detected. Stopping loop."

        return True, None

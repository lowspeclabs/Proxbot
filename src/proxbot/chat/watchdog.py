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

"""Key names, aliases, modifiers, and RFB event encoding."""

import re
import struct
from typing import Any

# Allowlist for simple key names accepted by send-key and send-keys. Characters
# in the printable ASCII range are added below.
BASE_KEY_ALLOWLIST = {
    "Enter", "Tab", "Ctrl+C", "Ctrl+D", "Escape",
    "Up", "Down", "Left", "Right", "Space",
    "Home", "End", "Page_Up", "Page_Down", "Delete", "BackSpace", "Insert",
    "Print", "Pause", "Scroll_Lock", "Sys_Req", "Menu", "Caps_Lock", "Num_Lock",
    "Ctrl", "Shift", "Alt", "Meta", "Super",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
}
BASE_KEY_ALLOWLIST.update(chr(c) for c in range(32, 127) if c not in {9, 10, 13})

# Modifier names accepted as the left-hand side of a combo (e.g., Shift+Tab).
_MODIFIER_ALLOWLIST = {"Ctrl", "Shift", "Alt", "Meta", "Super", "Win", "Control"}

# Common pre-defined combos. Combos are validated generically, so this list is
# only for error-message hints and quick documentation.
COMMON_COMBO_ALLOWLIST = {
    "Shift+Tab", "Ctrl+Alt+T", "Ctrl+Shift+T", "Ctrl+Shift+N",
    "Alt+Tab", "Alt+F4", "Ctrl+Alt+Del", "Ctrl+Alt+Delete",
    "Ctrl+Alt+F1", "Ctrl+Alt+F2", "Ctrl+Alt+F7",
    "Ctrl+A", "Ctrl+C", "Ctrl+D", "Ctrl+E", "Ctrl+U", "Ctrl+X", "Ctrl+V", "Ctrl+Z",
    "Ctrl+Shift+C", "Ctrl+Shift+V", "Super+T", "Super+D", "Super+R",
}

SEND_KEY_ALLOWLIST = set(BASE_KEY_ALLOWLIST)
SEND_KEY_ALLOWLIST.update(COMMON_COMBO_ALLOWLIST)

TYPE_ALLOWED_CONTROLS = {"\n", "\t", "\r"}

# This layout is intentionally versioned. It is valid only when tasksel focus
# is on the selected desktop task in the stock Debian 13 installer.
INSTALL_PRESETS = {
    "debian-13-server-v1": [
        {"action": "key", "key": "Space"},
        {"action": "keys", "keys": "Downx10,Space,Tab,Enter"},
    ],
}

US_SHIFTED_TYPE_CHARS = {
    "~": "`",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}

# X11 keysyms used for VNC key events.
X11_KEYSYM = {
    "Enter": 0xFF0D,
    "Tab": 0xFF09,
    "Escape": 0xFF1B,
    "Pause": 0xFF13,
    "Scroll_Lock": 0xFF14,
    "Sys_Req": 0xFF15,
    "Up": 0xFF52,
    "Down": 0xFF54,
    "Left": 0xFF51,
    "Right": 0xFF53,
    "Space": 0x0020,
    "Control_L": 0xFFE3,
    "Shift_L": 0xFFE1,
    "Alt_L": 0xFFE9,
    "Meta_L": 0xFFE7,
    "Super_L": 0xFFEB,
    "Shift_R": 0xFFE2,
    "Alt_R": 0xFFEA,
    "Control_R": 0xFFE4,
    "Meta_R": 0xFFE8,
    "Super_R": 0xFFEC,
    "F1": 0xFFBE,
    "F2": 0xFFBF,
    "F3": 0xFFC0,
    "F4": 0xFFC1,
    "F5": 0xFFC2,
    "F6": 0xFFC3,
    "F7": 0xFFC4,
    "F8": 0xFFC5,
    "F9": 0xFFC6,
    "F10": 0xFFC7,
    "F11": 0xFFC8,
    "F12": 0xFFC9,
    "Home": 0xFF50,
    "End": 0xFF57,
    "Page_Up": 0xFF55,
    "Page_Down": 0xFF56,
    "Delete": 0xFFFF,
    "BackSpace": 0xFF08,
    "Insert": 0xFF63,
    "Print": 0xFF61,
    "Menu": 0xFF67,
    "Caps_Lock": 0xFFE5,
    "Num_Lock": 0xFF7F,
}

KEY_TOKEN_ALIASES = {
    "return": "Enter",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "space": "Space",
    "spacebar": "Space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "bksp": "BackSpace",
    "bs": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "ins": "Insert",
    "home": "Home",
    "end": "End",
    "pgup": "Page_Up",
    "pageup": "Page_Up",
    "page-up": "Page_Up",
    "page_up": "Page_Up",
    "prior": "Page_Up",
    "pgdn": "Page_Down",
    "pagedown": "Page_Down",
    "page-down": "Page_Down",
    "page_down": "Page_Down",
    "next": "Page_Down",
    "up": "Up",
    "uparrow": "Up",
    "arrowup": "Up",
    "down": "Down",
    "downarrow": "Down",
    "arrowdown": "Down",
    "left": "Left",
    "leftarrow": "Left",
    "arrowleft": "Left",
    "right": "Right",
    "rightarrow": "Right",
    "arrowright": "Right",
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "ctl": "Ctrl",
    "ctr": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "option": "Alt",
    "meta": "Meta",
    "super": "Super",
    "win": "Super",
    "windows": "Super",
    "winkey": "Super",
    "cmd": "Meta",
    "command": "Meta",
    "print": "Print",
    "printscreen": "Print",
    "prtsc": "Print",
    "sysrq": "Sys_Req",
    "sysreq": "Sys_Req",
    "sys-req": "Sys_Req",
    "pause": "Pause",
    "break": "Pause",
    "scrolllock": "Scroll_Lock",
    "scroll-lock": "Scroll_Lock",
    "scroll_lock": "Scroll_Lock",
    "menu": "Menu",
    "app": "Menu",
    "application": "Menu",
    "context": "Menu",
    "capslock": "Caps_Lock",
    "caps-lock": "Caps_Lock",
    "caps_lock": "Caps_Lock",
    "numlock": "Num_Lock",
    "num-lock": "Num_Lock",
    "num_lock": "Num_Lock",
}
for _idx in range(1, 13):
    KEY_TOKEN_ALIASES[f"f{_idx}"] = f"F{_idx}"

# Modifier keys used for combo parsing.
_MODIFIER_KEYS = {
    "Ctrl": "Control_L",
    "Shift": "Shift_L",
    "Alt": "Alt_L",
    "Meta": "Meta_L",
    "Super": "Super_L",
    "Win": "Super_L",
    "Control": "Control_L",
}

# Valid buttons for the VNC pointer event.
VNC_BUTTON_LEFT = 1
VNC_BUTTON_MIDDLE = 2
VNC_BUTTON_RIGHT = 4
VNC_BUTTON_SCROLL_UP = 8
VNC_BUTTON_SCROLL_DOWN = 16


RUN_COMMAND_ALLOWLIST = {
    "ip addr",
    "ip link",
    "ip route",
    "hostname",
    "uname -a",
    "uptime",
    "df -h",
    "free -m",
    "ps aux",
    "ls -la",
    "cat /etc/os-release",
    "systemctl status ssh",
    "systemctl status cron",
    "dmesg",
    "journalctl -xe",
    "ping -c 1 8.8.8.8",
    "ss -tlnp",
    "netstat -tlnp",
    "whoami",
    "id",
    "pwd",
    "lsblk",
}


SHELL_METACHARACTERS_RE = re.compile(r"[;&|<>`$(){}\[\]\\'\"\n]")


# ---------------------------------------------------------------------------
# Key token normalization
# ---------------------------------------------------------------------------
def _strip_key_wrappers(token: str) -> str:
    """Remove surrounding whitespace, quotes, backticks, and [] wrappers.

    Bracket notation may wrap the whole token (`[ctrl]+[alt]+[del]`) or each
    part individually, so brackets are peeled recursively until stable.
    """
    tok = token.strip()
    for _ in range(2):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'`":
            tok = tok[1:-1].strip()
    while len(tok) >= 2 and tok[0] == "[" and tok[-1] == "]":
        tok = tok[1:-1].strip()
    return tok.replace("[", "").replace("]", "").strip()


def _canonical_single_key_token(token: str) -> str:
    """Normalize one non-combo key token to its canonical name."""
    tok = _strip_key_wrappers(token)
    if not tok:
        return ""
    if tok in X11_KEYSYM:
        return tok
    alias = KEY_TOKEN_ALIASES.get(tok)
    if alias:
        return alias
    alias = KEY_TOKEN_ALIASES.get(tok.lower())
    if alias:
        return alias
    if len(tok) == 1:
        return tok
    return tok


def _looks_like_modifier(token: str) -> bool:
    """True when the token names a modifier key (Ctrl/Alt/Shift/...)."""
    return _canonical_single_key_token(token) in _MODIFIER_KEYS


def _split_key_combo(token: str) -> list[str]:
    """Split a combo on '+' while respecting [bracketed] parts."""
    parts: list[str] = []
    current = ""
    depth = 0
    for ch in token:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == "+" and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += ch
    parts.append(current)
    return [p for p in (p.strip() for p in parts) if p]


def _canonical_key_token(token: str) -> str:
    """Normalize a full key token, including combos in + or - notation."""
    tok = _strip_key_wrappers(token)
    if not tok:
        return ""
    if "+" in tok:
        parts = _split_key_combo(tok)
    elif "-" in tok and not tok.startswith("-") and len(tok) > 1 and _looks_like_modifier(tok.split("-")[0]):
        parts = [p for p in tok.split("-") if p]
    else:
        return _canonical_single_key_token(tok)
    if len(parts) <= 1:
        return _canonical_single_key_token(parts[0]) if parts else ""
    return "+".join(_canonical_single_key_token(p) for p in parts)


def _key_name_to_events(key: str) -> list[tuple[int, int]]:
    """Return [(keysym, is_press), ...] for a single non-modifier key."""
    name = _canonical_single_key_token(key)
    if not name:
        return []
    if name in X11_KEYSYM:
        keysym = X11_KEYSYM[name]
        return [(keysym, 1), (keysym, 0)]
    if len(name) == 1:
        ch = name
        if ch.isalpha() and ch.isupper():
            shift = X11_KEYSYM["Shift_L"]
            lower = ord(ch.lower())
            return [(shift, 1), (lower, 1), (lower, 0), (shift, 0)]
        return [(ord(ch), 1), (ord(ch), 0)]
    return []


def _combo_to_events(combo: str) -> list[tuple[int, int]]:
    """Return [(keysym, is_press), ...] for a (possibly modified) key token."""
    canonical = _canonical_key_token(combo)
    if not canonical:
        return []
    if "+" not in canonical:
        return _key_name_to_events(canonical)
    parts = canonical.split("+")
    modifiers = [p for p in parts[:-1] if p in _MODIFIER_KEYS]
    base = parts[-1]
    if base in _MODIFIER_KEYS:
        return _key_name_to_events(base)
    events: list[tuple[int, int]] = []
    for mod in modifiers:
        keysym = X11_KEYSYM[_MODIFIER_KEYS[mod]]
        events.append((keysym, 1))
    base_events = _key_name_to_events(base)
    if not base_events:
        return []
    events.extend(base_events)
    for mod in reversed(modifiers):
        keysym = X11_KEYSYM[_MODIFIER_KEYS[mod]]
        events.append((keysym, 0))
    return events


def _is_valid_key_token(token: str) -> bool:
    """True when the token normalizes to an allowlisted key or combo."""
    canonical = _canonical_key_token(token)
    if not canonical:
        return False
    if canonical in SEND_KEY_ALLOWLIST:
        return True
    if canonical in COMMON_COMBO_ALLOWLIST:
        return True
    if "+" in canonical:
        parts = canonical.split("+")
        base = parts[-1]
        if not (base in SEND_KEY_ALLOWLIST or base in X11_KEYSYM or len(base) == 1):
            return False
        return all(p in _MODIFIER_KEYS for p in parts[:-1])
    return canonical in X11_KEYSYM or len(canonical) == 1


def _parse_key_sequence(keys_str: str) -> list[str] | None:
    """Parse a comma-separated key list, expanding `KeyxN` repetition."""
    keys: list[str] = []
    for raw in keys_str.split(","):
        token = raw.strip()
        if not token:
            continue
        repeat = 1
        match = re.match(r"^(.*?)(?:x(\d+))$", token, re.IGNORECASE)
        if match and match.group(1).strip():
            token = match.group(1).strip()
            repeat = int(match.group(2))
            if repeat < 1:
                return None
        if not _is_valid_key_token(token):
            return None
        keys.extend([token] * repeat)
    return keys or None


def _key_sequence_summary(keys: list[str]) -> dict[str, int]:
    """Count occurrences per canonical key for reporting."""
    summary: dict[str, int] = {}
    for key in keys:
        canonical = _canonical_key_token(key)
        summary[canonical] = summary.get(canonical, 0) + 1
    return summary


def _send_key_events(vnc: "_VNCClient", events: list[tuple[int, int]]) -> bool:
    """Send a prepared key event list through the VNC client."""
    if not vnc or not events:
        return False
    ok = True
    for keysym, is_press in events:
        payload = struct.pack(">BBHI", 0, 0, 0, keysym) if is_press else struct.pack(">BBHI", 1, 0, 0, keysym)
        if not vnc._send(payload):
            ok = False
    return ok


def _text_to_events(text: str) -> list[tuple[int, int]]:
    """Convert literal text into key events, shifting where required."""
    events: list[tuple[int, int]] = []
    shift = X11_KEYSYM["Shift_L"]
    for ch in text:
        if ch in TYPE_ALLOWED_CONTROLS:
            # Map the few allowed control characters onto their named keys.
            named = {"\n": "Enter", "\r": "Enter", "\t": "Tab"}.get(ch)
            if named:
                events.extend(_key_name_to_events(named))
            continue
        base = US_SHIFTED_TYPE_CHARS.get(ch, ch)
        if len(base) != 1:
            continue
        needs_shift = base.isalpha() and base.isupper()
        if needs_shift:
            # RFB convention: press Shift and send the lowercase keysym.
            events.append((shift, 1))
            base = base.lower()
        keysym = ord(base)
        events.append((keysym, 1))
        events.append((keysym, 0))
        if needs_shift:
            events.append((shift, 0))
    return events


def _type_text_has_shell_metacharacters(text: str) -> bool:
    """True when text contains shell metacharacters (blocked unless --unsafe)."""
    return bool(SHELL_METACHARACTERS_RE.search(text or ""))


def _validate_macro_action(action: Any, allow_unsafe: bool) -> tuple[str, dict[str, Any] | None]:
    """Validate one macro action; return (error, normalized_action)."""
    if not isinstance(action, dict):
        return "macro actions must be JSON objects", None
    kind = action.get("action")
    if kind == "key":
        key = action.get("key")
        if not isinstance(key, str) or not key.strip():
            return "macro key actions require a 'key' string", None
        if not _is_valid_key_token(key):
            return f"key '{key}' is not in the allowlist", None
        return "", {"action": "key", "key": _canonical_key_token(key)}
    if kind == "keys":
        keys = action.get("keys")
        if not isinstance(keys, str) or not keys.strip():
            return "macro keys actions require a 'keys' string", None
        parsed = _parse_key_sequence(keys)
        if parsed is None:
            return f"invalid key sequence '{keys}'", None
        return "", {"action": "keys", "keys": ",".join(parsed)}
    if kind == "type":
        text = action.get("text")
        if not isinstance(text, str) or not text:
            return "macro type actions require a non-empty 'text' string", None
        if not allow_unsafe and _type_text_has_shell_metacharacters(text):
            return "macro type text contains shell metacharacters; use --unsafe to allow", None
        return "", {"action": "type", "text": text}
    if kind == "click":
        x, y = action.get("x"), action.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return "macro click actions require integer 'x' and 'y'", None
        return "", {"action": "click", "x": x, "y": y}
    if kind == "move":
        x, y = action.get("x"), action.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return "macro move actions require integer 'x' and 'y'", None
        return "", {"action": "move", "x": x, "y": y}
    if kind == "wait":
        seconds = action.get("seconds", 1)
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return "macro wait actions require a non-negative 'seconds' value", None
        return "", {"action": "wait", "seconds": seconds}
    return f"unsupported macro action '{kind}'", None

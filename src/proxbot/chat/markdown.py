"""Terminal markdown rendering, ANSI colors, and prompt helpers."""

import re
import shutil
import unicodedata
from typing import List, Optional

# ANSI Terminal Colors & Styling
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
GRAY = "\033[90m"

BG_RED = "\033[41m"
BG_GREEN = "\033[42m"
BG_YELLOW = "\033[43m"
BG_BLUE = "\033[44m"
BG_MAGENTA = "\033[45m"
BG_CYAN = "\033[46m"
BG_DARK = "\033[48;5;236m"

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
STRIP_ANSI_RE = re.compile(r"[\x01\x02]|\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for accurate length calculation."""
    return STRIP_ANSI_RE.sub("", text)


def readline_prompt(text: str) -> str:
    """Return prompt text formatted for interactive input."""
    return strip_ansi(text)


def prompt_input(prompt_text: str = "") -> str:
    """Prompt for user input with clean prompt string to prevent ANSI escape code corruption in readline/PyREPL."""
    clean_prompt = strip_ansi(prompt_text) if prompt_text else ""
    return input(clean_prompt)


def render_markdown(text: str) -> str:
    """Render markdown with Unicode box tables, code blocks, headers, and colors."""
    if not text:
        return ""

    lines = text.split("\n")
    rendered_lines: List[str] = []
    in_code_block = False
    code_block_lang = ""
    code_block_lines: List[str] = []
    table_lines: List[str] = []

    def _terminal_width() -> int:
        try:
            w = shutil.get_terminal_size(fallback=(100, 24)).columns
            return max(40, min(int(w), 160))
        except Exception:
            return 100

    def _display_width(text: str) -> int:
        """Visible terminal columns for text (ANSI stripped, wide emoji/CJK = 2)."""
        clean = strip_ansi(text)
        total = 0
        for ch in clean:
            if unicodedata.combining(ch):
                continue
            if unicodedata.east_asian_width(ch) in ("W", "F"):
                total += 2
                continue
            o = ord(ch)
            # Common wide emoji / symbol ranges (e.g. U+274C ❌ is N/EastAsian
            # Narrow but renders 2 cols in most terminals).
            if (
                (0x2190 <= o <= 0x21FF)
                or (0x2300 <= o <= 0x23FF)
                or (0x25A0 <= o <= 0x27BF)
                or (0x2B00 <= o <= 0x2BFF)
                or (0x1F000 <= o <= 0x1FAFF)
                or o in (0x2611, 0x2612, 0x26A0, 0x2714, 0x2716, 0x274C, 0x274E, 0xFE0F)
            ):
                total += 2
            else:
                total += 1
        return total

    def _strip_md_markers(text: str) -> str:
        """Remove inline markdown markers for width/display measurement."""
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"__([^_]+)__", r"\1", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
        return text

    def _visible_width(text: str) -> int:
        return _display_width(_strip_md_markers(text))

    def _split_row(line: str) -> List[str]:
        """Split a markdown table row on unescaped pipes outside `code` spans."""
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|") and not s.endswith("\\|"):
            s = s[:-1]
        cols: List[str] = []
        cur: List[str] = []
        in_code = False
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
                cur.append("|")
                i += 2
                continue
            if ch == "`":
                in_code = not in_code
                cur.append(ch)
                i += 1
                continue
            if ch == "|" and not in_code:
                cols.append("".join(cur).strip())
                cur = []
                i += 1
                continue
            cur.append(ch)
            i += 1
        cols.append("".join(cur).strip())
        return cols

    def _is_separator_row(cols: List[str]) -> bool:
        non_empty = [c for c in cols if c]
        if not non_empty:
            return False
        return all(re.match(r"^:?-+:?$", c) for c in non_empty)

    def _is_table_line(line: str) -> bool:
        """Tolerant detector: any line containing a pipe (outer pipes optional)."""
        s = line.strip()
        if not s or "|" not in s:
            return False
        # Avoid bullet/numbered list markers or shell prose without structure?
        # e.g. `echo a | b` is a single pipe with no separator nearby — it is
        # rejected later at flush time (no separator row -> no box table).
        return True

    def _wrap_cell(text: str, width: int) -> List[str]:
        width = max(3, int(width))
        if not text:
            return [""]
        # Preserve explicit newlines inside a cell, wrap each part by display
        # width. Break only at spaces; hard-break overlong tokens. Never
        # break at hyphens so template names / URLs stay copy-pasteable.
        parts: List[str] = []
        for para in text.split("\n"):
            if not para.strip():
                parts.append("")
                continue
            if _visible_width(para) <= width:
                parts.append(para)
                continue
            words = para.split(" ")
            cur = ""
            cur_w = 0
            for w in words:
                ww = _visible_width(w)
                if ww > width:
                    if cur:
                        parts.append(cur)
                        cur = ""
                        cur_w = 0
                    # Hard-break overlong token, measuring by visible width
                    # so markdown markers don't force premature splits.
                    chunk = ""
                    for ch in w:
                        if _visible_width(chunk + ch) > width:
                            parts.append(chunk)
                            chunk = ch
                        else:
                            chunk += ch
                    cur = chunk
                    cur_w = _visible_width(chunk)
                    continue
                sep_w = _visible_width(" ") if cur else 0
                if cur_w + sep_w + ww > width:
                    parts.append(cur)
                    cur = w
                    cur_w = ww
                else:
                    cur = (cur + " " + w) if cur else w
                    cur_w += sep_w + ww
            parts.append(cur)
        return parts or [""]

    def _fit_col_widths(natural: List[int], max_total: int, num_cols: int) -> List[int]:
        """Shrink widest columns first so the table fits max_total display cols."""
        fitted = list(natural)
        # Each column occupies width+2 padding plus 1 border char; +1 for leading border.
        total = sum(w + 2 for w in fitted) + num_cols + 1
        if total <= max_total:
            return fitted
        min_col = 8
        # If even minimums don't fit (many columns), allow narrower floor of 3.
        if (sum(min_col + 2 for _ in fitted) + num_cols + 1) > max_total:
            min_col = 3
        available = max_total - num_cols - 1 - (2 * num_cols)
        available = max(available, min_col * num_cols)
        # Iteratively shave the current widest shrinkable column until it fits.
        guard = 0
        while sum(fitted) > available and guard < 10000:
            guard += 1
            candidates = [i for i, w in enumerate(fitted) if w > min_col]
            if not candidates:
                break
            widest = max(candidates, key=lambda i: fitted[i])
            fitted[widest] -= 1
        return fitted

    def flush_table(tbl_lines: List[str], max_width: Optional[int] = None) -> List[str]:
        if not tbl_lines:
            return []
        rows: List[List[str]] = []
        has_separator = False
        for l in tbl_lines:
            cols = _split_row(l)
            if _is_separator_row(cols):
                has_separator = True
                continue
            rows.append(cols)

        if not rows:
            return []
        if not has_separator:
            # Not a real markdown table (e.g. shell prose `echo a | b`).
            # Return lines with inline styles instead of a broken box.
            return [_apply_inline_styles(l) for l in tbl_lines]

        # Header defines the column count: truncate extras into the last
        # column, pad short rows. Prevents one ragged row from adding
        # phantom empty columns to the whole table.
        num_cols = max(1, len(rows[0]))
        fixed: List[List[str]] = []
        for r in rows:
            if len(r) > num_cols:
                r = r[: num_cols - 1] + [" | ".join(r[num_cols - 1 :])]
            while len(r) < num_cols:
                r.append("")
            fixed.append(r)
        rows = fixed
        col_widths = [0] * num_cols
        for r in rows:
            for i in range(num_cols):
                col_widths[i] = max(col_widths[i], _visible_width(r[i]))

        col_widths = [max(w, 3) for w in col_widths]

        # Constrain to terminal width so long cells wrap instead of
        # overflowing and breaking the box borders via terminal line-wrap.
        term_width = max_width or _terminal_width()
        col_widths = _fit_col_widths(col_widths, term_width, num_cols)

        # Wrap every cell to its (possibly shrunk) column width.
        wrapped_rows: List[List[List[str]]] = []
        for r in rows:
            wrapped_row: List[List[str]] = []
            for i in range(num_cols):
                val = r[i] if i < len(r) else ""
                wrapped_row.append(_wrap_cell(val, col_widths[i]))
            wrapped_rows.append(wrapped_row)

        out: List[str] = []
        top_border = f"{CYAN}┌" + "┬".join("─" * (w + 2) for w in col_widths) + f"┐{RESET}"
        out.append(top_border)

        for row_idx, wr in enumerate(wrapped_rows):
            height = max(len(c) for c in wr)
            for line_no in range(height):
                formatted_cells = []
                for i in range(num_cols):
                    frag = wr[i][line_no] if line_no < len(wr[i]) else ""
                    if row_idx == 0:
                        clean = _strip_md_markers(frag)
                        pad = " " * max(0, (col_widths[i] - _display_width(clean)))
                        formatted_cells.append(f" {BOLD}{WHITE}{clean}{RESET}{pad} ")
                    else:
                        styled_frag = _apply_inline_styles(frag)
                        pad = " " * max(0, (col_widths[i] - _visible_width(frag)))
                        formatted_cells.append(f" {styled_frag}{pad} ")
                row_str = f"{CYAN}│{RESET}" + f"{CYAN}│{RESET}".join(formatted_cells) + f"{CYAN}│{RESET}"
                out.append(row_str)

            if row_idx == 0 and len(wrapped_rows) > 1:
                mid_border = f"{CYAN}├" + "┼".join("─" * (w + 2) for w in col_widths) + f"┤{RESET}"
                out.append(mid_border)

        bot_border = f"{CYAN}└" + "┴".join("─" * (w + 2) for w in col_widths) + f"┘{RESET}"
        out.append(bot_border)
        return out

    for line in lines:
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_lang = line[3:].strip()
                code_block_lines = []
                if table_lines:
                    rendered_lines.extend(flush_table(table_lines))
                    table_lines = []
            else:
                in_code_block = False
                header = f"{GRAY}┌── {CYAN}{code_block_lang or 'code'}{GRAY} " + ("─" * max(10, 45 - len(code_block_lang))) + f"{RESET}"
                rendered_lines.append(header)
                for cb_l in code_block_lines:
                    rendered_lines.append(f"{GRAY}│{RESET}  {GREEN}{cb_l}{RESET}")
                rendered_lines.append(f"{GRAY}└──" + ("─" * 48) + f"{RESET}")
                code_block_lines = []
                code_block_lang = ""
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        if _is_table_line(line):
            table_lines.append(line)
            continue
        elif table_lines:
            if not line.strip():
                # Blank line inside/after a table: defer flush so a single
                # blank doesn't split one table into two boxes.
                continue
            rendered_lines.extend(flush_table(table_lines))
            table_lines = []

        if line.startswith("### "):
            rendered_lines.append(f"\n{BOLD}{CYAN}◆ {line[4:]}{RESET}")
            continue
        if line.startswith("## "):
            rendered_lines.append(f"\n{BOLD}{MAGENTA}■ {line[3:]}{RESET}")
            continue
        if line.startswith("# "):
            rendered_lines.append(f"\n{BOLD}{YELLOW}█ {line[2:]}{RESET}")
            continue

        if re.match(r"^\s*[-*]\s+", line):
            indent = len(line) - len(line.lstrip())
            bullet_text = line.lstrip()[2:]
            rendered_lines.append(f"{' ' * indent}{CYAN}•{RESET} {_apply_inline_styles(bullet_text)}")
            continue

        num_match = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if num_match:
            indent, num, content = num_match.groups()
            rendered_lines.append(f"{indent}{YELLOW}{num}.{RESET} {_apply_inline_styles(content)}")
            continue

        rendered_lines.append(_apply_inline_styles(line))

    if table_lines:
        rendered_lines.extend(flush_table(table_lines))

    if in_code_block and code_block_lines:
        header = f"{GRAY}┌── {CYAN}{code_block_lang or 'code'}{GRAY} " + ("─" * 35) + f"{RESET}"
        rendered_lines.append(header)
        for cb_l in code_block_lines:
            rendered_lines.append(f"{GRAY}│{RESET}  {GREEN}{cb_l}{RESET}")
        rendered_lines.append(f"{GRAY}└──" + ("─" * 40) + f"{RESET}")

    return "\n".join(rendered_lines)


def _apply_inline_styles(text: str) -> str:
    """Apply inline styling (bold, italic, code, status badges)."""
    text = re.sub(r"`([^`]+)`", rf"{BOLD}{YELLOW}\1{RESET}", text)
    text = re.sub(r"\*\*([^*]+)\*\*", rf"{BOLD}\1{RESET}", text)
    text = re.sub(r"__([^_]+)__", rf"{BOLD}\1{RESET}", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", rf"{ITALIC}\1{RESET}", text)

    badges = {
        r"\b(running|online|active|healthy|ok|passed)\b": f"{GREEN}\\1{RESET}",
        r"\b(stopped|offline|inactive|failed|error|dead)\b": f"{RED}\\1{RESET}",
        r"\b(warning|paused|suspended|degraded)\b": f"{YELLOW}\\1{RESET}",
    }
    for pattern, repl in badges.items():
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    return text

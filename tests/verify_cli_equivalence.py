#!/usr/bin/env python3
"""Verify the refactored CLI is behavior-identical to the pre-refactor script.

Compares every function/class source between the original flat script and the
extracted `src/proxbot` modules, ignoring module-level import/constant lines.
Any difference in a function body is reported.
"""
import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# Known, intentional divergences from the pre-refactor script.
# cmd_vm_exec: EXIT_API_ERROR (undefined constant) replaced with EXIT_API.
KNOWN_CHANGES = {"cmd_vm_exec"}

ORIG = pathlib.Path("/tmp/opencode/Proxmox-cli.py.orig")
# Only the CLI-side modules correspond to the original CLI script. The chat and
# console packages share helper names (strip_ansi, render_markdown, log) with
# different bodies, so they must be excluded to keep this comparison meaningful.
NEW_FILES = sorted((REPO / "src" / "proxbot" / "common").rglob("*.py")) + sorted(
    (REPO / "src" / "proxbot" / "cli").rglob("*.py")
)


def defs(path: pathlib.Path) -> dict[str, str]:
    """Return qualified-name -> source for every def/class in a module.

    Names are qualified by enclosing class (and file) because several helpers
    (strip_ansi, render_markdown, log, __init__) legitimately exist in more
    than one module with different bodies.
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    out: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                seg = "".join(lines[child.lineno - 1 : child.end_lineno])
                out[f"{prefix}{child.name}"] = seg
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = "".join(lines[child.lineno - 1 : child.end_lineno])
                out[f"{prefix}{child.name}"] = seg

    visit(tree, "")
    return out


def normalize(seg: str) -> str:
    """Strip comments and blank lines so formatting moves don't count as changes."""
    kept = []
    for line in seg.splitlines():
        s = line.split("#", 1)[0].rstrip() if not line.strip().startswith("#") else ""
        if s.strip():
            kept.append(s.rstrip())
    return "\n".join(kept)


def main() -> int:
    if not ORIG.exists():
        print(f"original backup not found at {ORIG}")
        return 2

    orig = defs(ORIG)
    new: dict[str, str] = {}
    for f in NEW_FILES:
        for name, seg in defs(f).items():
            new.setdefault(name, seg)

    shared = sorted(set(orig) & set(new))
    changed = []
    for name in shared:
        if normalize(orig[name]) != normalize(new[name]):
            changed.append(name)

    print(f"definitions compared: {len(shared)}")
    print(f"identical:            {len(shared) - len(changed)}")
    print(f"changed:              {len(changed)}")
    for name in changed:
        print(f"  CHANGED {name}")

    unexpected = [n for n in changed if n not in KNOWN_CHANGES]
    if KNOWN_CHANGES & set(changed):
        print(f"known intentional change(s): {sorted(KNOWN_CHANGES & set(changed))}")
    if unexpected:
        print(f"UNEXPECTED changes: {unexpected}")

    missing = sorted(set(orig) - set(new))
    if missing:
        print(f"present only in original: {missing}")

    return 1 if (unexpected or missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())

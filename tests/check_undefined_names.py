#!/usr/bin/env python3
"""Report module-level names referenced but never defined/imported in a module.

Only considers loads that resolve to a module global (i.e. not bound anywhere
inside the function/class scope chain), so locals and parameters are ignored.
"""
import ast
import builtins
import pathlib
import sys


def _bound_names(node: ast.AST, into: set[str]) -> None:
    """Collect every name bound anywhere inside `node` (params, locals, walrus)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            into.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            into.add(n.name)
            if n is not node:
                _bound_names(n, into)
        elif isinstance(n, ast.arg):
            into.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            into.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                into.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            into.update(n.names)


def module_scope(tree: ast.Module) -> set[str]:
    names: set[str] = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        names.add(a.asname or a.name.split(".")[0])
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        for n in ast.walk(t):
                            if isinstance(n, ast.Name):
                                names.add(n.id)
    return names


def missing_globals(tree: ast.Module) -> set[str]:
    known = module_scope(tree)
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        inner: set[str] = set()
        _bound_names(node, inner)
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id not in inner and n.id not in known:
                    out.add(n.id)
    return out


def main() -> int:
    roots = [pathlib.Path(p) for p in sys.argv[1:]] or [pathlib.Path("src/proxbot")]
    files: list[pathlib.Path] = []
    for r in roots:
        files.extend(sorted(r.rglob("*.py")) if r.is_dir() else [r])
    bad = False
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"SYNTAX {f}: {exc}")
            bad = True
            continue
        missing = sorted(missing_globals(tree))
        if missing:
            print(f"{f}: {missing}")
            bad = True
    if not bad:
        print("no undefined module globals")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

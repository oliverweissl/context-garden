"""Python indexing via the stdlib `ast` module. No third-party dependency.

Extracts, per file: imports (module names, best-effort resolved to
in-repo files later by pr_index), and top-level/nested function & class
symbols with their line ranges, first docstring line, and the names they
call (name-based, not type-resolved -- see references/scoring.md for why
that's an accepted approximation).
"""

from __future__ import annotations

import ast


def parse_python_file(text: str) -> dict:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"imports": [], "symbols": [], "parse_error": True}

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            imports.append(module)

    symbols = []

    def visit(node, parent_qualname=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                qualname = f"{parent_qualname}.{child.name}" if parent_qualname else child.name
                doc = ast.get_docstring(child) or ""
                doc_first_line = doc.strip().splitlines()[0] if doc.strip() else ""
                calls = sorted(set(_collect_calls(child)) | set(_collect_annotation_names(child)))
                end_line = getattr(child, "end_lineno", child.lineno)
                symbols.append(
                    {
                        "name": child.name,
                        "qualname": qualname,
                        "type": kind,
                        "start_line": child.lineno,
                        "end_line": end_line,
                        "doc": doc_first_line,
                        "calls": calls,
                    }
                )
                visit(child, qualname)
            else:
                visit(child, parent_qualname)

    visit(tree)
    return {"imports": sorted(set(imports)), "symbols": symbols, "parse_error": False}


def _collect_calls(node) -> list[str]:
    """Calls made directly within `node`'s own body -- does NOT descend into
    nested function/class defs, since those get their own symbol entries
    with their own calls list (otherwise a class's calls would include
    every one of its methods' calls, duplicated)."""
    names = []

    def walk(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            walk(child)

    walk(node)
    return names


def _name_of(expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _collect_annotation_names(node) -> list[str]:
    """Parameter/return type annotations reference a type by name without
    ever "calling" it (e.g. `def f(bounds: Range) -> Range:`), so the plain
    Call-based _collect_calls above would miss the connection between a
    function and a type it operates on. Treating annotations as references
    too lets that type's definition surface via graph distance."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    names = []
    args = node.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if a.annotation is not None:
            n = _name_of(a.annotation)
            if n:
                names.append(n)
    if node.returns is not None:
        n = _name_of(node.returns)
        if n:
            names.append(n)
    return names

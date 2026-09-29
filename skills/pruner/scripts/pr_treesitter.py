"""Optional tree-sitter parsing backend.

Used only when `tree_sitter` + `tree_sitter_python`/`tree_sitter_cpp` import.
Same record shape as pr_python / pr_cpp (`imports`, `symbols`, `parse_error`);
error tolerant, and inline C++ methods get a `Class::method` qualname.
Targets py-tree-sitter 0.22-0.25 (older `Parser().set_language` as fallback;
0.26.0 excluded, see _version_supported). Any failure returns None and the
caller falls back to the builtin parser for that file.
"""

from __future__ import annotations

import ast

_LANG_MODULES = {"python": "tree_sitter_python", "cpp": "tree_sitter_cpp"}
_parsers: dict[str, object] = {}
_languages: dict[str, object] = {}
_PRIMITIVES = {"auto", "void", "const", "static", "inline", "virtual", "typename"}


def _parser(language: str):
    if language in _parsers:
        return _parsers[language]
    parser = None
    try:
        import importlib

        import tree_sitter

        if not _version_supported():
            raise ImportError("unsupported py-tree-sitter version")

        mod = importlib.import_module(_LANG_MODULES[language])
        lang = tree_sitter.Language(mod.language())
        _languages[language] = lang  # the Parser may not keep its Language alive
        try:
            parser = tree_sitter.Parser(lang)
        except TypeError:  # py-tree-sitter < 0.22
            parser = tree_sitter.Parser()
            parser.set_language(lang)
    except Exception:  # noqa: BLE001 -- optional backend, any failure -> builtin
        parser = None
    _parsers[language] = parser
    return parser


SUPPORTED = ((0, 22), (0, 26))  # [min, max) py-tree-sitter versions


def _version_supported() -> bool:
    """py-tree-sitter 0.26.0 segfaults on node access partway through real
    files (reproduced on IPython/core/alias.py); 0.24/0.25 are tested."""
    try:
        from importlib.metadata import version

        parts = tuple(int(x) for x in version("tree-sitter").split(".")[:2])
    except Exception:  # noqa: BLE001
        return False
    return SUPPORTED[0] <= parts < SUPPORTED[1]


def available() -> bool:
    return all(_parser(lang) is not None for lang in _LANG_MODULES)


def parse(language: str, text: str) -> dict | None:
    parser = _parser(language)
    if parser is None:
        return None
    global _SRC
    # keep the source bytes alive for the tree's lifetime and slice node
    # text from them ourselves: `Node.text` on a tree whose source buffer
    # was a temporary has been seen to segfault
    _SRC = text.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(_SRC)
        if language == "python":
            return _python(tree.root_node)
        return _cpp(tree.root_node)
    except Exception:  # noqa: BLE001
        return None
    finally:
        _SRC = b""


_SRC = b""


def _t(node) -> str:
    if node is None:
        return ""
    return _SRC[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _field(node, name: str):
    return node.child_by_field_name(name) if node is not None else None


def _row(point) -> int:
    return (point.row if hasattr(point, "row") else point[0]) + 1


def _walk_no_defs(node, stop_types, visit):
    for child in node.children:
        visit(child)
        if child.type not in stop_types:
            _walk_no_defs(child, stop_types, visit)


# ---------------------------------------------------------------- Python

_PY_DEFS = {"function_definition", "class_definition", "decorated_definition"}


def _py_last_name(node) -> str | None:
    if node is None:
        return None
    if node.type == "type" and node.named_children:
        node = node.named_children[0]
    if node.type == "identifier":
        return _t(node)
    if node.type == "attribute":
        return _t(_field(node, "attribute"))
    return None


def _py_doc(body) -> str:
    if body is None or not body.named_children:
        return ""
    first = body.named_children[0]
    if first.type != "expression_statement" or not first.named_children:
        return ""
    s = first.named_children[0]
    if s.type != "string":
        return ""
    try:
        doc = ast.literal_eval(_t(s))
    except (ValueError, SyntaxError):
        return ""
    doc = doc.strip() if isinstance(doc, str) else ""
    return doc.splitlines()[0] if doc else ""


def _python(root) -> dict:
    imports: list[str] = []
    symbols: list[dict] = []

    def collect_imports(node):
        for child in node.children:
            if child.type == "import_statement":
                for n in child.named_children:
                    if n.type == "dotted_name":
                        imports.append(_t(n))
                    elif n.type == "aliased_import":
                        imports.append(_t(_field(n, "name")))
            elif child.type == "import_from_statement":
                imports.append(_t(_field(child, "module_name")).replace(" ", ""))
            else:
                collect_imports(child)

    def refs_of(defn) -> list[str]:
        names: list[str] = []

        def visit(n):
            if n.type == "call":
                name = _py_last_name(_field(n, "function"))
                if name:
                    names.append(name)

        body = _field(defn, "body")
        if body is not None:
            _walk_no_defs(body, _PY_DEFS, visit)
        if defn.type == "function_definition":
            params = _field(defn, "parameters")
            for p in params.named_children if params is not None else []:
                name = _py_last_name(_field(p, "type"))
                if name:
                    names.append(name)
            name = _py_last_name(_field(defn, "return_type"))
            if name:
                names.append(name)
        return sorted(set(names))

    def visit(node, parent_q):
        for child in node.children:
            defn, start = child, child
            if child.type == "decorated_definition":
                defn = _field(child, "definition")
            if defn is None or defn.type not in ("function_definition", "class_definition"):
                visit(child, parent_q)
                continue
            name = _t(_field(defn, "name"))
            qual = f"{parent_q}.{name}" if parent_q else name
            symbols.append(
                {
                    "name": name,
                    "qualname": qual,
                    "type": "class" if defn.type == "class_definition" else "function",
                    "start_line": _row(start.start_point),
                    "end_line": _row(defn.end_point),
                    "doc": _py_doc(_field(defn, "body")),
                    "calls": refs_of(defn),
                }
            )
            visit(defn, qual)

    collect_imports(root)
    visit(root, "")
    return {"imports": sorted(set(imports)), "symbols": symbols, "parse_error": root.has_error}


# ---------------------------------------------------------------- C / C++

_CPP_TYPES = {"class_specifier", "struct_specifier", "enum_specifier", "union_specifier"}


def _cpp_declarator_name(decl) -> str | None:
    """Drill through pointer/reference declarators to the function_declarator's name."""
    while decl is not None and decl.type != "function_declarator":
        decl = _field(decl, "declarator")
    if decl is None:
        return None
    inner = _field(decl, "declarator")
    return "".join(_t(inner).split()) if inner is not None else None


def _cpp_callee(fn) -> str | None:
    if fn is None:
        return None
    if fn.type in ("identifier", "field_identifier"):
        return _t(fn)
    if fn.type == "field_expression":
        return _t(_field(fn, "field"))
    if fn.type in ("qualified_identifier", "template_function"):
        return _cpp_callee(_field(fn, "name"))
    return None


def _cpp(root) -> dict:
    includes: list[str] = []
    symbols: list[dict] = []

    def type_refs(node, own: str) -> list[str]:
        out = []

        def visit(n):
            if n.type in ("type_identifier", "identifier") and _t(n) != own:
                if _t(n) not in _PRIMITIVES:
                    out.append(_t(n))
            for c in n.children:
                visit(c)

        if node is not None:
            visit(node)
        return out

    def visit(node, class_q):
        for child in node.children:
            start = node if node.type == "template_declaration" else child
            if child.type == "preproc_include":
                path = _t(_field(child, "path"))
                includes.append(path.strip('"<>'))
            elif child.type == "function_definition":
                qual = _cpp_declarator_name(_field(child, "declarator"))
                if not qual:
                    visit(child, class_q)
                    continue
                if class_q and "::" not in qual:
                    qual = f"{class_q}::{qual}"
                name = qual.split("::")[-1]
                calls = []

                def on(n, calls=calls):
                    if n.type == "call_expression":
                        callee = _cpp_callee(_field(n, "function"))
                        if callee:
                            calls.append(callee)

                body = _field(child, "body")
                if body is not None:
                    _walk_no_defs(body, set(), on)
                decl = _field(child, "declarator")
                params = decl
                while params is not None and params.type != "function_declarator":
                    params = _field(params, "declarator")
                calls += type_refs(_field(child, "type"), name)
                if params is not None:
                    calls += type_refs(_field(params, "parameters"), name)
                symbols.append(
                    {
                        "name": name,
                        "qualname": qual,
                        "type": "function",
                        "start_line": _row(start.start_point),
                        "end_line": _row(child.end_point),
                        "doc": "",
                        "calls": sorted(set(calls)),
                    }
                )
            elif child.type in _CPP_TYPES and _field(child, "body") is not None:
                name_node = _field(child, "name")
                name = _t(name_node) if name_node is not None else ""
                if name:
                    symbols.append(
                        {
                            "name": name,
                            "qualname": f"{class_q}::{name}" if class_q else name,
                            "type": "type",
                            "start_line": _row(start.start_point),
                            "end_line": _row(child.end_point),
                            "doc": "",
                            "calls": [],
                        }
                    )
                visit(_field(child, "body"), name or class_q)
            else:
                visit(child, class_q)

    visit(root, "")
    symbols.sort(key=lambda s: s["start_line"])
    return {"imports": includes, "symbols": symbols, "parse_error": root.has_error}

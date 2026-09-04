"""Lightweight C/C++ indexing: brace-depth state machine + regex, no libclang
dependency. This is a heuristic, not a real parser -- it does not expand
macros, understand templates deeply, resolve overloads, or account for
braces inside string/char literals or comments. It is good enough to
recover function name + line range + a naive call list, which is what
selection scoring needs. See references/scoring.md for the tradeoff.
"""
from __future__ import annotations

import re

CONTROL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "sizeof", "else",
    "do", "new", "delete", "throw", "static_cast", "dynamic_cast",
    "reinterpret_cast", "const_cast", "typeof", "decltype",
}
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
NAME_BEFORE_PAREN_RE = re.compile(r"([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
TYPE_DECL_RE = re.compile(r"^\s*(?:struct|class|enum(?:\s+class)?)\s+([A-Za-z_]\w*)\b")
IDENTIFIER_RE = re.compile(r"\b([A-Za-z_]\w*)\b")
_PRIMITIVE_KEYWORDS = {
    "int", "double", "float", "char", "bool", "void", "long", "short",
    "unsigned", "signed", "const", "static", "inline", "virtual",
    "constexpr", "auto", "return", "struct", "class", "enum", "template",
    "typename", "namespace", "using", "friend", "explicit", "override",
    "noexcept", "public", "private", "protected",
}


def _signature_type_refs(sig_text: str, own_name: str) -> list[str]:
    """Identifier-shaped tokens in a function's return type + parameter
    list, e.g. the `Matrix` in `double residual(const Matrix& m)`. This is
    intentionally over-inclusive (it also picks up parameter names like
    `m`, `tolerance`) -- those simply fail to resolve to any indexed
    symbol later and are silently dropped, so being sloppy here is safe
    and cheap, and it's what lets a referenced type's definition surface
    via graph distance for C/C++ (see references/scoring.md)."""
    before_parens = sig_text.split("(", 1)[0]
    after_first_paren = sig_text[len(before_parens):]
    tokens = IDENTIFIER_RE.findall(before_parens) + IDENTIFIER_RE.findall(after_first_paren)
    return [t for t in tokens if t != own_name and t not in _PRIMITIVE_KEYWORDS]


def _extract_type_symbols(lines: list[str]) -> list[dict]:
    """Independent pass for struct/class/enum declarations (kind='type').
    Deliberately separate from the function-detection state machine below:
    a type can contain inline member functions, which the function pass
    already captures as their own nested symbols -- some line overlap
    between a type symbol and its member-function symbols is expected and
    harmless for selection purposes (see references/scoring.md)."""
    symbols = []
    i, n = 0, len(lines)
    while i < n:
        m = TYPE_DECL_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        start = i
        j = i
        brace_line = None
        while j < n:
            if "{" in lines[j]:
                brace_line = j
                break
            if lines[j].rstrip().endswith(";"):
                break  # forward declaration, no body to index
            j += 1
        if brace_line is None:
            i += 1
            continue
        depth = 0
        end = None
        k = brace_line
        while k < n:
            depth += lines[k].count("{") - lines[k].count("}")
            if depth <= 0:
                end = k
                break
            k += 1
        if end is None:
            i += 1
            continue
        symbols.append({
            "name": name, "qualname": name, "type": "type",
            "start_line": start + 1, "end_line": end + 1,
            "doc": "", "calls": [],
        })
        i = end + 1
    return symbols


def _extract_function_name(sig_text: str) -> str | None:
    matches = NAME_BEFORE_PAREN_RE.findall(sig_text)
    if not matches:
        return None
    return matches[-1]


def _collect_calls(body_text: str) -> list[str]:
    names = [m for m in CALL_RE.findall(body_text) if m not in CONTROL_KEYWORDS]
    return sorted(set(names))


def parse_cpp_file(text: str) -> dict:
    lines = text.splitlines()
    includes = []
    symbols = []

    buffer: list[str] = []
    buffer_start_line = None
    in_body = False
    depth = 0
    current_qualname = None
    body_start_line = None
    body_lines: list[str] = []

    def finalize(end_line: int):
        nonlocal in_body, current_qualname, body_lines
        simple = current_qualname.split("::")[-1]
        # Exclude the signature portion of the opening line from
        # call-scanning (the function's own name immediately precedes '('
        # there, which would otherwise register as a spurious self-call on
        # every function) but keep anything after the '{' on that same
        # line, since a one-liner body lives entirely on it.
        first_line = body_lines[0]
        sig_part, _, after_brace = first_line.partition("{")
        call_text = "\n".join([after_brace] + body_lines[1:])
        calls = sorted(set(_collect_calls(call_text)) | set(_signature_type_refs(sig_part, simple)))
        symbols.append({
            "name": simple,
            "qualname": current_qualname,
            "type": "function",
            "start_line": body_start_line,
            "end_line": end_line,
            "doc": "",
            "calls": calls,
        })
        in_body = False
        current_qualname = None
        body_lines = []

    for i, line in enumerate(lines, start=1):
        m = INCLUDE_RE.match(line)
        if m:
            includes.append(m.group(1))
            continue
        stripped = line.strip()

        if not in_body:
            if not stripped:
                buffer = []
                buffer_start_line = None
                continue
            if buffer_start_line is None:
                buffer_start_line = i
            if "{" in line:
                sig_prefix = line.split("{", 1)[0]
                sig_text = " ".join(buffer + [sig_prefix])
                qualname = _extract_function_name(sig_text)
                simple = qualname.split("::")[-1] if qualname else None
                sig_start = buffer_start_line if buffer_start_line is not None else i
                buffer = []
                buffer_start_line = None
                if not simple or simple in CONTROL_KEYWORDS:
                    continue
                current_qualname = qualname
                body_start_line = sig_start
                in_body = True
                depth = line.count("{") - line.count("}")
                # keep the full (possibly multi-line) signature text as the
                # first "body line" so _signature_type_refs sees parameter
                # types declared on earlier lines too, not just this one
                body_lines = [sig_text + "{" + line.split("{", 1)[1]]
                if depth <= 0:
                    finalize(i)  # one-liner: opened and closed on the same line
            elif stripped.endswith((";", "}")):
                buffer = []
                buffer_start_line = None
            else:
                buffer.append(line)
        else:
            depth += line.count("{") - line.count("}")
            body_lines.append(line)
            if depth <= 0:
                finalize(i)

    symbols.extend(_extract_type_symbols(lines))
    symbols.sort(key=lambda s: s["start_line"])
    return {"imports": includes, "symbols": symbols, "parse_error": False}

"""Heuristic C/C++ indexing (brace-depth state machine + regex, no libclang):
no macro expansion, shallow templates, no overload resolution. Literals and
comments are blanked (same length, newlines kept) before brace counting.
Recovers name + line range + a naive call list. See references/scoring.md.
"""

from __future__ import annotations

import re

CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "else",
    "do",
    "new",
    "delete",
    "throw",
    "static_cast",
    "dynamic_cast",
    "reinterpret_cast",
    "const_cast",
    "typeof",
    "decltype",
}
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
NAME_BEFORE_PAREN_RE = re.compile(
    r"((?:[A-Za-z_]\w*::)*operator\s*(?:\(\s*\)|\[\s*\]|[+\-*/%^&|~!=<>,]+|new|delete)"
    r"|[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\("
)
RAW_STRING_RE = re.compile(r'R"([^()\\\s]{0,16})\(')
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
TYPE_DECL_RE = re.compile(r"^\s*(?:struct|class|enum(?:\s+class)?)\s+([A-Za-z_]\w*)\b")
IDENTIFIER_RE = re.compile(r"\b([A-Za-z_]\w*)\b")
_PRIMITIVE_KEYWORDS = {
    "int",
    "double",
    "float",
    "char",
    "bool",
    "void",
    "long",
    "short",
    "unsigned",
    "signed",
    "const",
    "static",
    "inline",
    "virtual",
    "constexpr",
    "auto",
    "return",
    "struct",
    "class",
    "enum",
    "template",
    "typename",
    "namespace",
    "using",
    "friend",
    "explicit",
    "override",
    "noexcept",
    "public",
    "private",
    "protected",
}


def _blank_literals_and_comments(text: str) -> str:
    """Replace the contents of string/char literals and comments with
    spaces (newlines kept), so line numbers and line lengths are preserved
    but braces/parens inside them no longer confuse the brace counter.
    Best-effort: handles "...", '...', R"delim(...)delim", // and /* */."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
        elif c == "R" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")) and (
            m := RAW_STRING_RE.match(text, i)
        ):
            close = ")" + m.group(1) + '"'
            j = text.find(close, m.end())
            j = n if j == -1 else j + len(close)
            blank(m.end(), j - len(close) if j < n else n)
            i = j
        elif c == '"' or (c == "'" and not (i > 0 and text[i - 1].isalnum())):
            # a ' right after an alnum is a C++14 digit separator (1'000)
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _signature_type_refs(sig_text: str, own_name: str) -> list[str]:
    """Identifier tokens in a function's return type + parameter list (the
    `Matrix` in `double residual(const Matrix& m)`); over-inclusive on purpose:
    parameter names like `m` just fail to resolve and are dropped."""
    before_parens = sig_text.split("(", 1)[0]
    after_first_paren = sig_text[len(before_parens) :]
    tokens = IDENTIFIER_RE.findall(before_parens) + IDENTIFIER_RE.findall(after_first_paren)
    return [t for t in tokens if t != own_name and t not in _PRIMITIVE_KEYWORDS]


def _extract_type_symbols(lines: list[str]) -> list[dict]:
    """struct/class/enum declarations (kind='type'), in a pass separate from
    function detection; overlap with nested member-function symbols is expected."""
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
        symbols.append(
            {
                "name": name,
                "qualname": name,
                "type": "type",
                "start_line": start + 1,
                "end_line": end + 1,
                "doc": "",
                "calls": [],
            }
        )
        i = end + 1
    return symbols


def _strip_ctor_init_list(sig_text: str) -> str:
    """`Vec(int n) : data_(n), size_(n)` -> `Vec(int n)`: cut at the first
    single `:` at paren depth 0 that follows a closing paren, so the name
    is taken from the constructor's own parameter list, not the last
    member initializer."""
    depth = 0
    seen_close = False
    for k, ch in enumerate(sig_text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            seen_close = True
        elif (
            ch == ":"
            and depth == 0
            and seen_close
            and sig_text[k - 1 : k] != ":"
            and sig_text[k + 1 : k + 2] != ":"
        ):
            return sig_text[:k]
    return sig_text


def _extract_function_name(sig_text: str) -> str | None:
    matches = NAME_BEFORE_PAREN_RE.findall(_strip_ctor_init_list(sig_text))
    if not matches:
        return None
    return re.sub(r"\s+(?=\W)|(?<=\W)\s+", "", matches[-1])


def _collect_calls(body_text: str) -> list[str]:
    names = [m for m in CALL_RE.findall(body_text) if m not in CONTROL_KEYWORDS]
    return sorted(set(names))


def parse_cpp_file(text: str) -> dict:
    raw_lines = text.splitlines()
    lines = _blank_literals_and_comments(text).splitlines()
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
        symbols.append(
            {
                "name": simple,
                "qualname": current_qualname,
                "type": "function",
                "start_line": body_start_line,
                "end_line": end_line,
                "doc": "",
                "calls": calls,
            }
        )
        in_body = False
        current_qualname = None
        body_lines = []

    for i, line in enumerate(lines, start=1):
        m = INCLUDE_RE.match(raw_lines[i - 1])
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

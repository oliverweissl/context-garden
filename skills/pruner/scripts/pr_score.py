"""Lexical/structural relevance scoring: IDF-weighted keyword overlap against
symbol names, qualnames, docstrings and paths. See references/scoring.md.
"""

from __future__ import annotations

import functools
import math
import os
import re
from pathlib import Path, PurePosixPath

_STOPWORDS = {
    "the", "a", "an", "in", "on", "of", "to", "for", "and", "or", "is", "are",
    "fix", "bug", "issue", "incorrect", "behavior", "when", "that", "this",
    "with", "error", "failing", "please", "need", "update", "change", "make",
    "should", "not", "it", "be", "at", "by", "from", "was", "were", "does",
    "do", "its", "into", "than", "then", "but", "so", "if", "as", "can",
    # language keywords that show up in echoed source lines / tracebacks
    "assert", "none", "true", "false", "def", "return", "self", "import",
    "class", "lambda", "raise", "const", "int", "double", "void", "auto",
    "std", "where", "declared", "scope", "line", "file", "most", "recent",
    "call", "last", "traceback",
}  # fmt: skip
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)")
_QUALNAME_RE = re.compile(r"[A-Za-z_]\w*(?:(?:\.|::)[A-Za-z_]\w*)+")

# gcc/clang: `file:N[:C]: error|warning|note|fatal error:` plus template
# backtrace lines like `file:N:C:   required from here`.
GCC_ERROR_RE = re.compile(
    r"([^\s:()']+):(\d+):(?:\d+:)?\s*(?:fatal error|error|warning|note)\b|"
    r"([^\s:()']+):(\d+):(?:\d+:)?\s+required from"
)
# MSVC: `x.cpp(12): error C2065: ...` / `C:\src\x.cpp(12,5): warning ...`
MSVC_ERROR_RE = re.compile(
    r"((?:[A-Za-z]:)?[^\s:()]+)\((\d+)(?:,\d+)?\)\s*:\s*(?:fatal error|error|warning|note)\b"
)
PY_TRACEBACK_RE = re.compile(r'File "([^"]+)", line (\d+)')
# pytest location lines: `interp/core.py:11: ZeroDivisionError` or a bare
# `tests/test_x.py:5: ` frame marker at the start of a line.
PYTEST_LOC_RE = re.compile(r"^\s*([^\s:()]+\.[A-Za-z0-9]+):(\d+):(?:\s|$)", re.MULTILINE)
# identifiers the error text itself names: exception types, pytest test
# ids, quoted identifiers ('residual', `foo`), traceback function names,
# and code echoed back by the tool (pytest `>` lines, gcc `NN | code`).
EXC_NAME_RE = re.compile(r"\b([A-Za-z_]\w*(?:Error|Exception|Warning|Failure))\b")
PYTEST_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+\S*?::([A-Za-z_][\w:]*)", re.MULTILINE)
QUOTED_IDENT_RE = re.compile(r"['`\u2018\"]([A-Za-z_][\w:]*)['\u2019\"]")
PY_FRAME_FUNC_RE = re.compile(r'File "[^"]+", line \d+, in ([A-Za-z_]\w*)')
ECHOED_CODE_RE = re.compile(r"^(?:>\s+|\s*\d+\s+\|\s)(.*)$", re.MULTILINE)
# build-directory hints for resolving short relative paths in diagnostics
DIR_HINT_RES = (
    re.compile(r"Entering directory [`'\"]([^'`\"]+)['`\"]"),
    re.compile(r"(?:^|\s)cd\s+([^\s;&]+)\s*(?:&&|;)", re.MULTILINE),
    re.compile(r"In file included from ([^\s:]+):\d+"),
)

ERROR_WINDOW = 15  # +/- lines around an error location that hits no symbol

GRAPH_K = 6  # BFS distances >= this score 0
EXACT_NAME_WEIGHT = 12.0
QUALNAME_WEIGHT = 12.0
PARENT_NAME_WEIGHT = 4.0
NAME_WORD_WEIGHT = 3.0


def split_identifier(token: str) -> list[str]:
    parts = token.split("_")
    out = []
    for p in parts:
        out.extend(m.lower() for m in _CAMEL_RE.findall(p) if m)
    return out


def stem(word: str) -> str:
    """Tiny suffix stripper so `clamped`/`clamps`/`clamping` meet `clamp`."""
    for suf in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


@functools.lru_cache(maxsize=65536)
def extract_keywords(text: str) -> frozenset[str]:
    words = _WORD_RE.findall(text or "")
    out = set()
    for w in words:
        out.add(w.lower())
        out.update(split_identifier(w))
    return frozenset(stem(w) for w in out if len(w) > 1 and w not in _STOPWORDS)


@functools.lru_cache(maxsize=262144)
def name_words(name: str) -> frozenset[str]:
    return frozenset(stem(w) for w in split_identifier(name) if len(w) > 1 and w not in _STOPWORDS)


@functools.lru_cache(maxsize=65536)
def _path_parts(rel: str) -> tuple[str, frozenset[str]]:
    p = PurePosixPath(rel)
    return p.stem, frozenset(x.lower() for x in p.with_suffix("").parts)


class Idf:
    """Inverse document frequency over chunk-name words (and whole names),
    normalized to (0, 1]: a word unique to one chunk weighs ~1, a word on
    every other chunk (`get`, `run`, `convert`) weighs little."""

    def __init__(self, docs: list[set[str]]):
        self.n = max(1, len(docs))
        self.df: dict[str, int] = {}
        for d in docs:
            for w in d:
                self.df[w] = self.df.get(w, 0) + 1
        self._norm = math.log(self.n + 1)

    def __call__(self, word: str) -> float:
        df = self.df.get(word, 0)
        if df == 0:
            return 1.0
        return max(0.05, min(1.0, math.log((self.n + 1) / (df + 0.5)) / self._norm))


class TaskText:
    """Pre-processed task (+ error identifiers) for fast per-chunk matching."""

    def __init__(self, text: str):
        self.raw = text or ""
        self.lower = self.raw.lower()
        self.words = {w.lower() for w in _WORD_RE.findall(self.raw)}
        self.qualnames = {q.lower().replace("::", ".") for q in _QUALNAME_RE.findall(self.raw)}
        self.keywords = extract_keywords(self.raw)
        # words used on their own vs. only as the tail of `Owner.name`
        bare = _QUALNAME_RE.sub(" ", self.raw)
        self.bare_words_cs = set(_WORD_RE.findall(bare))
        self.bare_words = {w.lower() for w in self.bare_words_cs}
        self.qual_owners: dict[str, set[str]] = {}
        for q in self.qualnames:
            parts = q.split(".")
            self.qual_owners.setdefault(parts[-1], set()).update(parts[:-1])


def parse_error_locations(error_text: str) -> list[tuple[str, int]]:
    """Returns (path_fragment, line) pairs found in gcc/clang/MSVC
    diagnostics, Python tracebacks, or pytest location lines. Paths are not
    yet resolved to repo-relative -- `resolve_error_locations` does that."""
    text = error_text or ""
    locs = []
    for m in GCC_ERROR_RE.finditer(text):
        path, line = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        locs.append((path, int(line)))
    for regex in (MSVC_ERROR_RE, PY_TRACEBACK_RE, PYTEST_LOC_RE):
        for m in regex.finditer(text):
            locs.append((m.group(1), int(m.group(2))))
    return list(dict.fromkeys(locs))


def error_identifiers(error_text: str) -> list[str]:
    """Identifiers the error text names (exception types, failing test
    names, quoted identifiers, traceback frame functions, identifiers in
    echoed source lines), used as extra task text for lexical scoring."""
    text = error_text or ""
    out = []
    for regex in (EXC_NAME_RE, PY_FRAME_FUNC_RE, QUOTED_IDENT_RE):
        out.extend(regex.findall(text))
    for m in PYTEST_FAILED_RE.finditer(text):
        out.extend(p for p in m.group(1).split("::") if p)
    for m in ECHOED_CODE_RE.finditer(text):
        out.extend(w for w in _WORD_RE.findall(m.group(1)) if len(w) > 1)
    return [i for i in dict.fromkeys(out) if i != "module" and i.lower() not in _STOPWORDS]


def failing_test_names(error_text: str) -> set[str]:
    names = set()
    for m in PYTEST_FAILED_RE.finditer(error_text or ""):
        names.update(p for p in m.group(1).split("::") if p)
    return names


def _dir_hints(error_text: str) -> list[str]:
    hints = []
    for regex in DIR_HINT_RES:
        for m in regex.finditer(error_text or ""):
            h = m.group(1).replace("\\", "/")
            hints.append(h if regex is not DIR_HINT_RES[2] else str(PurePosixPath(h).parent))
    return list(dict.fromkeys(hints))


def _suffix_len(a: list[str], b: list[str]) -> int:
    k = 0
    while k < len(a) and k < len(b) and a[-1 - k] == b[-1 - k]:
        k += 1
    return k


def match_error_path(frag: str, index, source_roots: set[str], by_base: dict) -> list[str]:
    """Repo files a diagnostic's path fragment can refer to, best first.
    Accepts exact / suffix matches in either direction (`solver.cpp` vs
    `engine/b/solver.cpp`, `/abs/repo/x/y.py` vs `x/y.py`) and installed-
    package paths (`.../site-packages/pkg/mod.py` -> `<root>/src/pkg/mod.py`
    when `<root>/src` is a Python source root). Keeps only the candidates
    sharing the longest path suffix."""
    parts = [p for p in os.path.normpath(frag.replace("\\", "/")).split("/") if p not in ("", ".")]
    while parts and parts[0] == "..":
        parts.pop(0)
    if not parts:
        return []
    scored = []
    for rel in by_base.get(parts[-1], []):
        rparts = rel.split("/")
        k = _suffix_len(parts, rparts)
        if k == len(rparts) or k == len(parts):
            scored.append((k, rel))
        elif k >= 2 and "/".join(rparts[:-k]) in source_roots:
            scored.append((k, rel))
    if not scored:
        return []
    best = max(k for k, _ in scored)
    return sorted(rel for k, rel in scored if k == best)


def _disambiguate(frag: str, line: int, cands: list[str], hints: list[str], idents: set[str], index):
    """Narrow several same-suffix candidates: (1) build-dir hints from the
    error text (`make: Entering directory`, `cd X &&`, include chains),
    (2) files whose symbols (or the symbol enclosing `line`) match other
    identifiers in the error. Returns (candidates, still_ambiguous)."""
    by_hint = []
    for rel in cands:
        for h in hints:
            joined = os.path.normpath(f"{h}/{frag}").replace("\\", "/")
            if joined == rel or joined.endswith("/" + rel):
                by_hint.append(rel)
                break
    if len(by_hint) == 1:
        return by_hint, False
    if by_hint:
        cands = by_hint
    if idents:
        scores = {}
        for rel in cands:
            syms = index.files.get(rel, {}).get("symbols", [])
            s = len(idents & {x["name"] for x in syms})
            for x in syms:
                if x["start_line"] <= line <= x["end_line"]:
                    s += len(idents & set(index.refs(x["id"])))
            scores[rel] = s
        top = max(scores.values())
        best = [r for r in cands if scores[r] == top]
        if top > 0 and len(best) == 1:
            return best, False
        if top > 0:
            cands = best
    return cands, len(cands) > 1


def resolve_error_locations(error_text: str, index, source_roots: set[str]):
    """Returns (ids, synthetic_chunks, notes): ids of symbols whose line
    range contains a parsed error location (innermost enclosing symbol
    first), plus -- for locations that hit no symbol (module-level code,
    files that failed to parse) -- the file's whole-file chunk if it is
    small, else a +/-ERROR_WINDOW line window; and human-readable notes
    about ambiguous path matches."""
    hits, synthetic, notes = [], {}, []
    by_base: dict[str, list[str]] = {}
    for rel in index.files:
        by_base.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    hints = _dir_hints(error_text)
    idents = set(error_identifiers(error_text))
    for frag, line in parse_error_locations(error_text):
        cands = match_error_path(frag, index, source_roots, by_base)
        if len(cands) > 1:
            cands, ambiguous = _disambiguate(frag, line, cands, hints, idents, index)
            if ambiguous:
                notes.append(
                    f"error path '{frag}' is ambiguous: matches {', '.join(cands)} "
                    "(selected all; pass a longer path or run from the build dir)"
                )
        for rel in cands:
            entry = index.files[rel]
            enclosing = [
                s for s in entry["symbols"] if s["start_line"] <= line <= s["end_line"]
            ]
            if enclosing:
                # innermost (method before its class)
                enclosing.sort(key=lambda s: s["end_line"] - s["start_line"])
                hits.extend(s["id"] for s in enclosing)
                continue
            n_lines = max(1, entry["line_count"])
            if not entry["symbols"] and n_lines <= 2 * ERROR_WINDOW + 1:
                hits.append(f"{rel}:__file__")
                continue
            start = max(1, line - ERROR_WINDOW)
            end = min(n_lines, line + ERROR_WINDOW)
            cid = f"{rel}:{start}-{end}"
            hits.append(cid)
            synthetic[cid] = {
                "id": cid,
                "file": rel,
                "start_line": start,
                "end_line": end,
                "name": Path(rel).name,
                "qualname": Path(rel).name,
                "doc": "",
                "chunk_kind": "window",
                "file_kind": entry["kind"],
                "tokens": None,
            }
    return list(dict.fromkeys(hits)), list(synthetic.values()), list(dict.fromkeys(notes))


def lexical_match(chunk: dict, task: TaskText, idf: Idf) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []
    name = chunk["name"]
    name_lower = name.lower()
    whole_file = chunk.get("chunk_kind") in ("file", "window")
    qual = name if whole_file else (chunk.get("qualname") or name).replace("::", ".")
    if "." in qual and qual.lower() in task.qualnames:
        score += QUALNAME_WEIGHT
        reasons.append(f"task text names '{qual}'")
    factor = 0.0
    if len(name_lower) > 2 and name_lower in task.bare_words:
        # exact-case mention counts fully; `command` vs class `Command` half
        factor = 1.0 if name in task.bare_words_cs else 0.5
    elif len(name_lower) > 2 and name_lower in task.qual_owners:
        # only mentioned as `Owner.name`: full credit if the owner is this
        # symbol's class/module/package, little otherwise (`Command.main`
        # says nothing about an unrelated `main`)
        owners = {p.lower() for p in qual.split(".")[:-1]}
        owners |= _path_parts(chunk["file"])[1]
        factor = 1.0 if owners & task.qual_owners[name_lower] else 0.25
    if factor and name_lower in _STOPWORDS:
        factor *= 0.25  # `fix`, `update`, `error`: task verbs, not symbol references
    if factor:
        w = idf("=" + name_lower)
        words = name_words(name)
        if len(words) == 1:
            # a one-word name is only as specific as that word is across
            # all names (`label` is rare as a whole name, common as a word)
            w = min(w, idf(next(iter(words))))
        score += EXACT_NAME_WEIGHT * w * factor
        rarity = "" if w > 0.7 else f" (common name, idf={w:.2f})"
        weak = "" if factor == 1.0 else f" (weak match x{factor})"
        reasons.append(f"task text directly names '{name}'{rarity}{weak}")
    else:
        overlap = name_words(name) & task.keywords
        if overlap:
            score += NAME_WORD_WEIGHT * sum(idf(w) for w in overlap)
            reasons.append(f"name shares keyword(s) {sorted(overlap)} with task")
    if "." in qual and not whole_file:
        parent = qual.rsplit(".", 2)[-2]
        if parent.lower() in task.words:
            score += PARENT_NAME_WEIGHT * idf("=" + parent.lower())
            reasons.append(f"enclosing '{parent}' is named in the task")
    doc = chunk.get("doc", "")
    doc_overlap = extract_keywords(doc) & task.keywords if doc else set()
    if doc_overlap:
        score += sum(idf(w) for w in doc_overlap)
        reasons.append(f"docstring shares keyword(s) {sorted(doc_overlap)} with task")
    path_overlap = set() if whole_file else name_words(_path_parts(chunk["file"])[0]) & task.keywords
    if path_overlap:
        score += 0.5 * len(path_overlap)
        reasons.append(f"file path shares keyword(s) {sorted(path_overlap)} with task")
    return score, reasons


def chunk_idf_docs(chunks: list[dict]) -> list[set[str]]:
    docs = []
    for c in chunks:
        d = name_words(c["name"]) | {"=" + c["name"].lower()}
        docs.append(d)
    return docs


def graph_score(distance: int | None) -> tuple[float, str | None]:
    if distance is None:
        return 0.0, None
    if distance == 0:
        # a seed always outranks its own 1-hop neighbours
        return float(GRAPH_K), "seed of the call-graph expansion"
    val = max(0, GRAPH_K - distance)
    if val <= 0:
        return 0.0, None
    return float(val), f"{distance} hop(s) from a seed symbol in the call/import graph"

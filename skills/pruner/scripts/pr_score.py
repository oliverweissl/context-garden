"""Deterministic, lexical/structural relevance scoring -- no embeddings, no
LLM calls. "Semantic relevance" from the spec is approximated by keyword
overlap against symbol names/qualnames/docstrings/paths; true semantic
understanding is out of scope for an offline, dependency-free tool (see
references/scoring.md).
"""

from __future__ import annotations

import re
from pathlib import Path

_STOPWORDS = {
    "the",
    "a",
    "an",
    "in",
    "on",
    "of",
    "to",
    "for",
    "and",
    "or",
    "is",
    "are",
    "fix",
    "bug",
    "issue",
    "incorrect",
    "behavior",
    "when",
    "that",
    "this",
    "with",
    "error",
    "failing",
    "please",
    "need",
    "update",
    "change",
    "make",
    "should",
    "not",
    "it",
    "be",
    "at",
    "by",
    "from",
}
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)")

GCC_ERROR_RE = re.compile(r"([^\s:()]+):(\d+):(?:\d+:)?\s*(?:error|warning):")
PY_TRACEBACK_RE = re.compile(r'File "([^"]+)", line (\d+)')

GRAPH_K = 6  # BFS distances >= this score 0


def split_identifier(token: str) -> list[str]:
    parts = token.split("_")
    out = []
    for p in parts:
        out.extend(m.lower() for m in _CAMEL_RE.findall(p) if m)
    return out


def extract_keywords(text: str) -> set[str]:
    words = _WORD_RE.findall(text or "")
    out = set()
    for w in words:
        out.add(w.lower())
        out.update(split_identifier(w))
    return {w for w in out if len(w) > 1 and w not in _STOPWORDS}


def parse_error_locations(error_text: str) -> list[tuple[str, int]]:
    """Returns (path_fragment, line) pairs found in GCC-style diagnostics
    or Python tracebacks. Paths are not yet resolved to repo-relative --
    the caller matches them against the index by suffix."""
    locs = []
    for m in GCC_ERROR_RE.finditer(error_text or ""):
        locs.append((m.group(1), int(m.group(2))))
    for m in PY_TRACEBACK_RE.finditer(error_text or ""):
        locs.append((m.group(1), int(m.group(2))))
    return locs


def resolve_error_locations(error_text: str, index: dict) -> list[str]:
    """Symbol ids whose line range contains a parsed error location."""
    hits = []
    for frag, line in parse_error_locations(error_text):
        frag_norm = frag.replace("\\", "/")
        for rel, entry in index["files"].items():
            if not (
                rel == frag_norm or rel.endswith("/" + frag_norm) or frag_norm.endswith("/" + rel)
            ):
                continue
            for sym in entry["symbols"]:
                if sym["start_line"] <= line <= sym["end_line"]:
                    hits.append(sym["id"])
    return hits


def lexical_match(
    name: str, doc: str, path: str, task_lower: str, task_keywords: set[str]
) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []
    name_lower = name.lower()
    if len(name_lower) > 2 and re.search(rf"\b{re.escape(name_lower)}\b", task_lower):
        score += 10.0
        reasons.append(f"task text directly names '{name}'")
    else:
        name_words = set(split_identifier(name))
        overlap = name_words & task_keywords
        if overlap:
            score += 3.0 * len(overlap)
            reasons.append(f"name shares keyword(s) {sorted(overlap)} with task")
    doc_overlap = extract_keywords(doc) & task_keywords
    if doc_overlap:
        score += 1.0 * len(doc_overlap)
        reasons.append(f"docstring shares keyword(s) {sorted(doc_overlap)} with task")
    path_words = set(split_identifier(Path(path).stem))
    path_overlap = path_words & task_keywords
    if path_overlap:
        score += 0.5 * len(path_overlap)
        reasons.append(f"file path shares keyword(s) {sorted(path_overlap)} with task")
    return score, reasons


def graph_score(distance: int | None) -> tuple[float, str | None]:
    if distance is None:
        return 0.0, None
    if distance == 0:
        return 0.0, None  # already scored as a lexical/error seed
    val = max(0, GRAPH_K - distance)
    if val <= 0:
        return 0.0, None
    return float(val), f"{distance} hop(s) from a seed symbol in the call/import graph"

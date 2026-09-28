"""Cheap contradiction check run at `promote` time.

Two facts in the same scope conflict when they talk about the same thing
(Jaccard overlap of normalised content words >= CONFLICT_JACCARD) with
opposite polarity (exactly one of them contains a negation such as
never / don't / do not / no / avoid / not). Deliberately lexical and
conservative: antonym pairs (tabs vs spaces) are out of scope.
"""

from __future__ import annotations

import re

CONFLICT_JACCARD = 0.4

NEGATIONS = frozenset(
    "never not no avoid don't dont doesn't doesnt do-not mustn't mustnt shouldn't "
    "shouldnt can't cant cannot won't wont without forbid forbidden disallow".split()
)
STOPWORDS = frozenset(
    "a an the and or but of to for in on at by with from into onto as is are was "
    "were be been it its this that these those there here than then so if when "
    "always must should use using used do does please only just all any each "
    "you we they i your our via per e g eg ie".split()
)
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("’", "'"))


def negated(text: str) -> bool:
    return any(t in NEGATIONS for t in _tokens(text))


def content_words(text: str) -> set[str]:
    out = set()
    for t in _tokens(text):
        if t in NEGATIONS or t in STOPWORDS or len(t) < 2:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # crude plural folding: builds == build
        out.add(t)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def conflicts(text: str, scope: str, facts: dict, skip_keys: set[str] = frozenset(),
              threshold: float = CONFLICT_JACCARD) -> list[tuple[str, float]]:
    """(fact_id, overlap) for every fact in `scope` that `text` contradicts."""
    words, neg = content_words(text), negated(text)
    out = []
    for fid, f in facts["facts"].items():
        if f.get("scope") != scope or f.get("key") in skip_keys:
            continue
        if negated(f["representation"]) == neg:
            continue
        overlap = jaccard(words, content_words(f["representation"]))
        if overlap >= threshold:
            out.append((fid, overlap))
    out.sort(key=lambda x: -x[1])
    return out

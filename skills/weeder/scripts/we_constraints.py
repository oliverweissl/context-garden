"""Functional-regression proxy: extract imperative/constraint sentences
("never...", "must...", "do not...") from a BEFORE skill's full text, and
check each one's key terms still appear SOMEWHERE in an AFTER skill's full
text (SKILL.md + all references combined -- moving a constraint into a
reference file is not a loss, since progressive disclosure keeps it
reachable, just not always-loaded).

This is a coverage heuristic, not comprehension: it can prove a
constraint's wording was deleted outright (the single most damaging and
most common failure mode of manual/LLM-driven compression), but it cannot
confirm subtler meaning was preserved. See references/routing-heuristic.md
for what a full functional check still requires (running a representative
task through a real agent before/after).
"""

from __future__ import annotations

import re

_CONSTRAINT_RE = re.compile(
    r"([^.!\n]*\b(never|always|must not|must|do not|don't|required|critical(?:ly)?|"
    r"cannot|shall not|prohibited|forbidden)\b[^.!\n]*[.!])",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the",
    "this",
    "that",
    "with",
    "when",
    "never",
    "always",
    "must",
    "not",
    "do",
    "don't",
    "required",
    "critical",
    "critically",
    "cannot",
    "shall",
    "prohibited",
    "forbidden",
    "and",
    "for",
    "are",
    "is",
    "was",
    "you",
    "your",
    "it",
    "its",
    "from",
    "into",
    "than",
    "then",
}


def extract_constraints(text: str) -> list[str]:
    seen = set()
    out = []
    for m in _CONSTRAINT_RE.finditer(text):
        s = " ".join(m.group(1).split())
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _keywords(sentence: str) -> set[str]:
    words = _WORD_RE.findall(sentence.lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def constraint_preserved(sentence: str, haystack_text: str, threshold: float = 0.6) -> bool:
    kws = _keywords(sentence)
    if not kws:
        return True
    haystack_words = _keywords(haystack_text)
    overlap = kws & haystack_words
    return (len(overlap) / len(kws)) >= threshold


def full_text(parsed: dict) -> str:
    """Everything reachable from this skill: SKILL.md body + every
    reference file, concatenated. Used as the "haystack" a constraint must
    appear somewhere in -- a move to references doesn't count as loss."""
    parts = [parsed["raw_body"]]
    parts.extend(parsed["references"].values())
    return "\n\n".join(parts)


def check_constraints_preserved(
    before_parsed: dict, after_parsed: dict, threshold: float = 0.6
) -> dict:
    before_text = full_text(before_parsed)
    after_text = full_text(after_parsed)
    constraints = extract_constraints(before_text)
    missing = [c for c in constraints if not constraint_preserved(c, after_text, threshold)]
    preserved_count = len(constraints) - len(missing)
    return {
        "total": len(constraints),
        "preserved": preserved_count,
        "missing": missing,
        "preserved_ratio": (preserved_count / len(constraints)) if constraints else 1.0,
    }

"""Functional-regression proxy: extract imperative/constraint sentences
("never...", "must...", "do not...") from a BEFORE skill's full text, one
per sentence (hard-wrapped lines joined), and check each one still appears
within a single sentence of an AFTER skill's full text -- key terms plus
the same negation/modal words and polarity (SKILL.md + all references
combined -- moving a constraint into a reference file is not a loss, since
progressive disclosure keeps it reachable, just not always-loaded).

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
    r"\b(never|always|must not|must|do not|don't|required|critical(?:ly)?|"
    r"cannot|shall not|prohibited|forbidden)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_BLOCK_START_RE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|```|~~~|\|)")
_CONTRACTIONS = {
    "don't": "do not",
    "doesn't": "does not",
    "can't": "can not",
    "cannot": "can not",
    "mustn't": "must not",
    "shouldn't": "should not",
    "shan't": "shall not",
    "won't": "will not",
    "isn't": "is not",
    "aren't": "are not",
}
_CONTRACTION_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in _CONTRACTIONS) + r")\b")
# Negation/modal words carry a constraint's force and polarity: they must
# appear in the SAME output sentence as the constraint's keywords, and the
# sentence's negation must match (so "Never X" -> "Always X" or
# "Must X" -> "Must not X" is caught, not passed as "same words").
_MARKERS = {"never", "always", "not", "must", "only", "required", "forbidden", "prohibited"}
_NEGATIONS = {"never", "not", "no", "nor"}
_STOPWORDS = {
    "the",
    "this",
    "that",
    "with",
    "when",
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


def _split_units(text: str) -> list[str]:
    """Sentences, with hard-wrapped lines inside a paragraph/list item
    joined first, so a constraint wrapped across lines is one sentence."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            blocks.append([])
        elif not blocks or not blocks[-1] or _BLOCK_START_RE.match(line):
            blocks.append([line.strip()])
        else:
            blocks[-1].append(line.strip())
    units = []
    for block in blocks:
        if block:
            units.extend(s for s in _SENTENCE_RE.split(" ".join(block)) if s.strip())
    return units


def _words(sentence: str) -> list[str]:
    lowered = _CONTRACTION_RE.sub(lambda m: _CONTRACTIONS[m.group(1)], sentence.lower())
    return _WORD_RE.findall(lowered)


def extract_constraints(text: str) -> list[str]:
    seen = set()
    out = []
    for unit in _split_units(text):
        if not _CONSTRAINT_RE.search(unit):
            continue
        s = " ".join(unit.split())
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _keywords(sentence: str) -> set[str]:
    return {w for w in _words(sentence) if len(w) > 3 and w not in _STOPWORDS | _MARKERS}


def _sentence_matches(kws: set[str], markers: set[str], negated: bool, candidate: str, threshold):
    words = set(_words(candidate))
    if not markers <= words or bool(words & _NEGATIONS) != negated:
        return False
    return not kws or len(kws & words) / len(kws) >= threshold


def constraint_preserved(sentence: str, haystack_text: str, threshold: float = 0.6) -> bool:
    """True if some single sentence of `haystack_text` carries the
    constraint's keywords (>= threshold coverage), every negation/modal
    marker it uses, and the same polarity -- not just its vocabulary
    scattered across the whole text."""
    words = set(_words(sentence))
    kws = _keywords(sentence)
    markers = words & _MARKERS
    negated = bool(words & _NEGATIONS)
    return any(
        _sentence_matches(kws, markers, negated, unit, threshold)
        for unit in _split_units(haystack_text)
    )


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

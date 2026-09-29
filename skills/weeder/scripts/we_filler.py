"""Filler-sentence detector for `audit`'s "likely unnecessary content".

Asymmetric by design: an imperative/constraint sentence is NEVER flagged
(deleting a real instruction is a silent regression; missed filler only costs
tokens), so unknown first words count as verbs. Rules: references/classification.md;
labelled fixture: tests/fixtures/filler_sentences.json.
"""

from __future__ import annotations

import re

from we_text import split_into_sentences

_CONSTRAINT_RE = re.compile(
    r"\b(?:must|never|always|do\s+not|don['’]t|only|should|required|ensure|run|use|"
    r"call|pass|set|avoid|prefer)\b",
    re.I,
)

# Technically imperative, but conversational padding rather than an instruction.
_PLEASANTRY_OPENERS = re.compile(
    r"^(?:please\s+)?(?:feel\s+free\b|note\s+that\b|note,|keep\s+in\s+mind\b|"
    r"remember\s+that\b|bear\s+in\s+mind\b|thanks?\b|hope\b|enjoy\b|good\s+luck\b)",
    re.I,
)

_NON_VERB_OPENERS = {
    # pronouns / determiners
    "this",
    "that",
    "these",
    "those",
    "it",
    "it's",
    "its",
    "there",
    "there's",
    "here",
    "here's",
    "we",
    "we're",
    "you",
    "you're",
    "you'll",
    "i",
    "i'm",
    "they",
    "he",
    "she",
    "our",
    "your",
    "their",
    "the",
    "a",
    "an",
    "some",
    "many",
    "most",
    "all",
    "each",
    "every",
    "any",
    "such",
    "one",
    "what",
    "which",
    "who",
    "why",
    "how",
    "where",
    # prepositions / conjunctions
    "in",
    "as",
    "of",
    "for",
    "at",
    "by",
    "with",
    "since",
    "because",
    "while",
    "although",
    "though",
    "if",
    "when",
    "once",
    "so",
    "and",
    "but",
    "or",
    "also",
    "to",
    "on",
    "from",
    "after",
    "before",
    "unlike",
    "like",
    "despite",
    # hedges / discourse markers / pleasantries
    "basically",
    "generally",
    "essentially",
    "typically",
    "usually",
    "overall",
    "ultimately",
    "honestly",
    "hopefully",
    "obviously",
    "clearly",
    "of",
    "great",
    "happy",
    "good",
    "nice",
    "perhaps",
    "maybe",
    "indeed",
    "again",
    "just",
    "now",
    "sometimes",
    "often",
    "however",
    "otherwise",
    "additionally",
    "furthermore",
    "moreover",
    "anyway",
    "welcome",
    "congratulations",
    "sure",
    "ideally",
}

_FILLER_PATTERNS = [
    # pleasantries
    re.compile(r"\bfeel free to\b", re.I),
    re.compile(r"\bhope (?:this|that|it) helps\b", re.I),
    re.compile(r"\bhappy \w+ing\b", re.I),
    re.compile(r"^(?:thanks|thank you)\b", re.I),
    re.compile(r"\bgood luck\b", re.I),
    re.compile(r"\bgreat question\b", re.I),
    re.compile(r"\bwe hope\b", re.I),
    re.compile(r"\bas an ai\b", re.I),
    # hedges / throat-clearing
    re.compile(r"\b(?:please )?note that\b", re.I),
    re.compile(
        r"\bit(?:'s| is) (?:important|worth|good) (?:to )?(?:note|noting|remember|mention)", re.I
    ),
    re.compile(r"\bkeep in mind\b", re.I),
    re.compile(r"\bbear in mind\b", re.I),
    re.compile(r"\byou (?:might|may|could) (?:want|wish|find|like)\b", re.I),
    re.compile(r"\bit (?:can|may|might|could) be (?:useful|helpful|handy|a good idea)\b", re.I),
    re.compile(r"^(?:basically|generally|essentially|honestly|obviously|of course)\b", re.I),
    re.compile(r"\bat the end of the day\b", re.I),
    re.compile(r"\bin order to\b", re.I),
    # rationale-only / self-description
    re.compile(r"\bthis skill (?:is designed to|helps you|will|aims to|exists to)\b", re.I),
    re.compile(
        r"^(?:this|that|it)(?:'s| is| was) (?:very |really |quite |especially )?"
        r"(?:useful|helpful|important|handy|nice|convenient|valuable|beneficial)\b",
        re.I,
    ),
    re.compile(r"^(?:this|that) (?:way|helps|makes|approach|means)\b", re.I),
    re.compile(r"^the (?:reason|idea|goal|point) (?:for|behind|of|is)\b", re.I),
]


def _first_word(sentence: str) -> str:
    m = re.match(r"[\s\"'(*_>]*([A-Za-z][A-Za-z'’]*)", sentence)
    return m.group(1).lower().replace("’", "'") if m else ""


def starts_with_base_verb(sentence: str) -> bool:
    s = re.sub(r"^(?:please|then|first|next|finally|now)[,\s]+", "", sentence.strip(), flags=re.I)
    if _PLEASANTRY_OPENERS.match(s):
        return False
    w = _first_word(s)
    if not w or w in _NON_VERB_OPENERS or "'" in w:
        return False
    if w.endswith(("ed", "ing", "ly")):
        return False
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return False
    return True


def is_instruction(sentence: str) -> bool:
    return bool(_CONSTRAINT_RE.search(sentence)) or starts_with_base_verb(sentence)


def is_filler(sentence: str) -> bool:
    s = sentence.strip()
    if not s or is_instruction(s):
        return False
    return any(p.search(s) for p in _FILLER_PATTERNS)


def find_filler_sentences(text: str) -> list[str]:
    return [s.strip() for s in split_into_sentences(text) if is_filler(s)]

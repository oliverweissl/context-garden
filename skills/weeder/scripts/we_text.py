"""Shared markdown-aware text cleaning for duplicate/filler detection.

Both we_dupes.py and we_audit.py need to turn a section's markdown into
plain prose before splitting into sentences -- left un-stripped, fenced
code blocks and heading/list markers glue onto adjacent text (they have
no terminal punctuation) and produce spurious matches: e.g. five CLI
usage examples that each start with `python3 .../weeder.py <subcommand>`
inside a fenced block will share that boilerplate and look like "the same
rule repeated five times" to a naive sentence splitter, which is exactly
the class of false positive this module exists to prevent. Caught by
running this tool on its own SKILL.md during development -- see
validate.md.
"""
from __future__ import annotations

import re

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+.*$", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def strip_code(text: str) -> str:
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return text


def strip_markdown_structure(text: str) -> str:
    """Code blocks + heading lines + leading list markers removed, safe to
    feed to a sentence splitter without structural elements gluing onto
    adjacent prose."""
    text = strip_code(text)
    text = _HEADING_LINE_RE.sub("", text)
    text = _LIST_MARKER_RE.sub("", text)
    return text


def split_into_sentences(text: str) -> list[str]:
    cleaned = strip_markdown_structure(text).replace("\n", " ")
    return [s.strip() for s in _SENTENCE_RE.split(cleaned) if s.strip()]

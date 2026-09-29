"""Shared markdown-aware text cleaning for duplicate/filler detection.

Code fences and heading/list markers have no terminal punctuation, so left in
they glue onto adjacent sentences and repeated usage examples look like
"the same rule repeated five times".
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


_BOUNDARY = "\x00"


def split_into_original_sentences(text: str) -> list[str]:
    """Like split_into_sentences, but each sentence is a verbatim span of
    `text` (inline code kept; only newlines flattened): code blocks, heading
    lines and list markers become sentence *boundaries* instead of being
    deleted mid-span. Duplicate-group members must be findable in the
    original file by apply-suggestion -- a sentence with its `inline code`
    stripped is not. Callers compute similarity on strip_code(sentence)."""
    marked = _CODE_FENCE_RE.sub(_BOUNDARY, text)
    marked = _HEADING_LINE_RE.sub(_BOUNDARY, marked)
    marked = _LIST_MARKER_RE.sub(_BOUNDARY, marked)
    out = []
    for block in marked.split(_BOUNDARY):
        out.extend(s.strip() for s in _SENTENCE_RE.split(block.replace("\n", " ")) if s.strip())
    return out

"""Token estimation, shared with the rest of this skill suite's chars/4
heuristic (see compost/seedbank/pruner) for consistency."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4)) if text else 0

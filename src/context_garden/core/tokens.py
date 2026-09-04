"""Token estimation and cost accounting.

Skills need a cheap, dependency-free way to estimate how many tokens a
piece of text costs, so they can decide what's worth keeping in context
versus offloading to an artifact. This is a rough heuristic, not a
tokenizer -- swap in a real tokenizer where accuracy matters more than
zero dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

_CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate for ``text`` (~4 characters per token)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


@dataclass
class TokenUsage:
    """A single recorded token cost, broken down by where it was spent."""

    input_tokens: int = 0
    repository_tokens: int = 0
    tool_result_tokens: int = 0
    skill_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.repository_tokens
            + self.tool_result_tokens
            + self.skill_tokens
            + self.output_tokens
        )

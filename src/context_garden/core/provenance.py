"""Provenance: link generated facts and summaries back to their sources.

Anything Context Garden promotes into persistent context (a cached fact, a
compressed trace, a verified claim) should be traceable back to the file,
command, hash, or artifact it came from. That link is what makes compressed
or cached context trustworthy instead of just "shorter."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Source:
    """One originating input for a piece of derived content."""

    type: str  # e.g. "file", "command", "artifact"
    uri: str
    content_hash: str | None = None


@dataclass
class ProvenanceRecord:
    """Associates a piece of generated content with the source(s) it came from."""

    claim: str
    sources: list[Source] = field(default_factory=list)
    generated_by: str | None = None  # e.g. skill or tool name
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "sources": [
                {"type": s.type, "uri": s.uri, "content_hash": s.content_hash}
                for s in self.sources
            ],
            "generated_by": self.generated_by,
            "created_at": self.created_at.isoformat(),
        }

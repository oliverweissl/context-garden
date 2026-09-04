"""Artifact storage: keep large outputs out of model context.

An artifact is any large piece of content a Skill wants to persist outside
the agent's context window -- a full compiler log, a large diff, a raw
profiler dump -- while still leaving it retrievable by a stable identifier.

This module defines the minimal interface plus a filesystem-backed
implementation. Storage is content-addressed (identifiers are derived from
a hash of the content), so identical content is only ever stored once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


def content_id(content: bytes) -> str:
    """Return the stable identifier for a piece of artifact content."""
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class ArtifactRef:
    """A stable pointer to stored content."""

    artifact_id: str
    size_bytes: int
    media_type: str = "text/plain"


class ArtifactStore:
    """Content-addressed artifact storage backed by a directory on disk.

    Layout: ``<root>/<artifact_id[:2]>/<artifact_id>``
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, artifact_id: str) -> Path:
        return self.root / artifact_id[:2] / artifact_id

    def put(self, content: bytes, media_type: str = "text/plain") -> ArtifactRef:
        """Store ``content`` and return a reference to it."""
        artifact_id = content_id(content)
        path = self._path_for(artifact_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return ArtifactRef(artifact_id=artifact_id, size_bytes=len(content), media_type=media_type)

    def get(self, artifact_id: str) -> bytes:
        """Retrieve the raw content for a previously stored artifact."""
        path = self._path_for(artifact_id)
        if not path.exists():
            raise KeyError(f"unknown artifact: {artifact_id}")
        return path.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._path_for(artifact_id).exists()

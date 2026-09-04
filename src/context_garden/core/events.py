"""The common context-activity event schema.

Every Context Garden Skill records what it does to the context in this
shared shape so that events from different Skills stay comparable and
interoperable. See ``docs/architecture.md`` for the rationale and
``docs/skill-development.md`` for how Skills are expected to emit events.

The schema is deliberately small and extensible: unknown/extra fields on
``Event.extra`` are preserved rather than rejected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """The supported event classes. New values may be added over time."""

    CONTEXT_ACCESS = "context_access"
    CONTEXT_GENERATED = "context_generated"
    CONTEXT_PROMOTED = "context_promoted"
    CONTEXT_INVALIDATED = "context_invalidated"
    TOOL_OUTPUT = "tool_output"
    VERIFICATION = "verification"
    SKILL_EXECUTION = "skill_execution"


@dataclass
class Resource:
    """The thing an event is about, e.g. a source range or a tool result."""

    type: str
    uri: str
    range: str | None = None


@dataclass
class Cost:
    """The estimated context cost associated with an event."""

    estimated_tokens: int | None = None


@dataclass
class Result:
    """The outcome of an event, e.g. a stored artifact."""

    artifact_id: str | None = None


@dataclass
class Event:
    """A single record of context-related activity.

    Mirrors the YAML example in the project plan:

        event: context_access
        resource: {type: source, uri: src/solver.cpp, range: 140-210}
        purpose: inspect_convergence_logic
        cost: {estimated_tokens: 720}
        result: {artifact_id: null}
        timestamp: ...
        session_id: ...
    """

    event: EventType
    resource: Resource
    purpose: str
    cost: Cost = field(default_factory=Cost)
    result: Result = field(default_factory=Result)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "resource": {
                "type": self.resource.type,
                "uri": self.resource.uri,
                "range": self.resource.range,
            },
            "purpose": self.purpose,
            "cost": {"estimated_tokens": self.cost.estimated_tokens},
            "result": {"artifact_id": self.result.artifact_id},
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        known = {"event", "resource", "purpose", "cost", "result", "timestamp", "session_id"}
        resource = data.get("resource") or {}
        cost = data.get("cost") or {}
        result = data.get("result") or {}
        timestamp = data.get("timestamp")
        return cls(
            event=EventType(data["event"]),
            resource=Resource(
                type=resource.get("type", "unknown"),
                uri=resource.get("uri", ""),
                range=resource.get("range"),
            ),
            purpose=data.get("purpose", ""),
            cost=Cost(estimated_tokens=cost.get("estimated_tokens")),
            result=Result(artifact_id=result.get("artifact_id")),
            session_id=data.get("session_id", str(uuid.uuid4())),
            timestamp=(
                datetime.fromisoformat(timestamp) if timestamp else datetime.now(UTC)
            ),
            extra={k: v for k, v in data.items() if k not in known},
        )

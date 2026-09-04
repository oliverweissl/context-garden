from context_garden.core.events import Cost, Event, EventType, Resource, Result


def _sample_event() -> Event:
    return Event(
        event=EventType.CONTEXT_ACCESS,
        resource=Resource(type="source", uri="src/solver.cpp", range="140-210"),
        purpose="inspect_convergence_logic",
        cost=Cost(estimated_tokens=720),
        result=Result(artifact_id=None),
    )


def test_all_event_classes_are_supported():
    expected = {
        "context_access",
        "context_generated",
        "context_promoted",
        "context_invalidated",
        "tool_output",
        "verification",
        "skill_execution",
    }
    assert {e.value for e in EventType} == expected


def test_event_round_trips_through_dict():
    event = _sample_event()
    data = event.to_dict()

    assert data["event"] == "context_access"
    assert data["resource"]["uri"] == "src/solver.cpp"
    assert data["cost"]["estimated_tokens"] == 720

    restored = Event.from_dict(data)
    assert restored.event == event.event
    assert restored.resource.uri == event.resource.uri
    assert restored.purpose == event.purpose


def test_event_preserves_unknown_fields_as_extra():
    data = {
        "event": "tool_output",
        "resource": {"type": "tool", "uri": "pytest"},
        "purpose": "run_tests",
        "future_field": "should not be dropped",
    }
    event = Event.from_dict(data)
    assert event.extra["future_field"] == "should not be dropped"
    assert event.to_dict()["future_field"] == "should not be dropped"

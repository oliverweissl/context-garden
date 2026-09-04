from context_garden.core.events import Cost, Event, EventType, Resource
from context_garden.core.store import LocalStore


def test_record_and_list_events(tmp_path):
    with LocalStore(tmp_path / ".context-garden" / "context.db") as store:
        event = Event(
            event=EventType.CONTEXT_ACCESS,
            resource=Resource(type="source", uri="src/solver.cpp"),
            purpose="inspect_convergence_logic",
            cost=Cost(estimated_tokens=720),
        )
        store.record_event(event)

        events = store.list_events()
        assert len(events) == 1
        assert events[0]["event"] == "context_access"
        assert events[0]["resource"]["uri"] == "src/solver.cpp"


def test_list_events_filters_by_type(tmp_path):
    with LocalStore(tmp_path / "context.db") as store:
        store.record_event(
            Event(
                event=EventType.CONTEXT_ACCESS,
                resource=Resource(type="source", uri="a.py"),
                purpose="read",
            )
        )
        store.record_event(
            Event(
                event=EventType.TOOL_OUTPUT,
                resource=Resource(type="tool", uri="pytest"),
                purpose="run_tests",
            )
        )

        assert len(store.list_events(event_type="tool_output")) == 1
        assert len(store.list_events()) == 2


def test_store_persists_across_reopen(tmp_path):
    db_path = tmp_path / "context.db"
    with LocalStore(db_path) as store:
        store.record_event(
            Event(
                event=EventType.SKILL_EXECUTION,
                resource=Resource(type="skill", uri="pruner"),
                purpose="select_relevant_files",
            )
        )

    with LocalStore(db_path) as store:
        assert len(store.list_events()) == 1

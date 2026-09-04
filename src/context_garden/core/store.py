"""Project-local persistence.

Backs the ``.context-garden/context.db`` convention: a small SQLite store
for events and provenance records, local to the repository using Context
Garden. This is deliberately minimal -- append events, list them back,
nothing more sophisticated yet (no querying/indexing layer).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from context_garden.core.events import Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim TEXT NOT NULL,
    generated_by TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class LocalStore:
    """SQLite-backed local state for a single repository.

    Typically opened at ``<repo>/.context-garden/context.db``.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record_event(self, event: Event) -> int:
        payload = json.dumps(event.to_dict())
        cur = self._conn.execute(
            "INSERT INTO events (event_type, session_id, timestamp, payload) "
            "VALUES (?, ?, ?, ?)",
            (event.event.value, event.session_id, event.timestamp.isoformat(), payload),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            rows = self._conn.execute("SELECT payload FROM events ORDER BY id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE event_type = ? ORDER BY id",
                (event_type,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LocalStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

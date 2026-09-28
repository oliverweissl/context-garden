"""SQLite-backed index store (stdlib `sqlite3`) + the read-side query API
that scoring/selection use.

Tables (see references/schema.md):
  meta(key, value)             version, parser, repo_root, indexed_at
  files(id, path, hash, mtime, size, kind, language, size_tokens,
        line_count, parse_error, imports, resolved_imports)
  symbols(id, file_id, name, qualname, type, start_line, end_line, doc,
          tokens, parent, dup)
  edges(src, name)             symbol row `src` references `name`

Integer row ids keep the store small; the string symbol ids used
everywhere else ("<path>:<qualname>", "@<line>" appended when `dup`) are
derived on load.

Edges are stored by *target name* and resolved to symbol ids lazily at
query time (`Index.neighbors`), using the same priority rules as before:
same file -> unique among imported files -> unique repo-wide. This keeps
incremental updates strictly per-file (no global re-resolution when one
file changes) and avoids the quadratic "resolve every call against every
same-named symbol" pass over common names like `get`/`run`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path, PurePosixPath

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE, hash TEXT, mtime REAL, size INTEGER,
    kind TEXT, language TEXT, size_tokens INTEGER, line_count INTEGER,
    parse_error INTEGER, imports TEXT, resolved_imports TEXT);
CREATE TABLE IF NOT EXISTS symbols(
    id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, qualname TEXT, type TEXT,
    start_line INTEGER, end_line INTEGER, doc TEXT, tokens INTEGER, parent INTEGER,
    dup INTEGER);
CREATE TABLE IF NOT EXISTS edges(src INTEGER, name TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_name ON edges(name);
"""
DROP_INDEXES = """
DROP INDEX IF EXISTS symbols_file;
DROP INDEX IF EXISTS edges_src;
DROP INDEX IF EXISTS edges_name;
"""

# reverse lookups ("who references `name`?") on names referenced from more
# than this many places are restricted to the target's own file and the
# files that import it -- `get`/`append`/`run` otherwise fan out to
# thousands of unrelated callers.
CALLER_FANOUT_CAP = 200


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        conn.executescript(SCHEMA + INDEXES)
    except sqlite3.DatabaseError:
        # a store from an older schema: start over (it is only a cache)
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA + INDEXES)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    """Drop and recreate every table (used on version/parser change)."""
    with conn:
        for table in ("meta", "files", "symbols", "edges"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(SCHEMA + INDEXES)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


class Index:
    """Read-side view of the store. Files and symbol metadata are loaded
    eagerly (one row each -- cheap even for ~100k symbols); references are
    queried and resolved on demand."""

    def __init__(self, conn: sqlite3.Connection, repo_root: Path):
        self.conn = conn
        self.repo_root = repo_root
        self.files: dict[str, dict] = {}
        path_of: dict[int, str] = {}
        for row in conn.execute(
            "SELECT id, path, kind, language, size_tokens, line_count, parse_error, "
            "resolved_imports FROM files"
        ):
            path_of[row[0]] = row[1]
            self.files[row[1]] = {
                "kind": row[2],
                "language": row[3],
                "size_tokens": row[4],
                "line_count": row[5],
                "parse_error": bool(row[6]),
                "resolved_imports": json.loads(row[7] or "[]"),
                "symbols": [],
            }
        self.by_id: dict[str, dict] = {}
        self.by_rowid: dict[int, dict] = {}
        self.by_name: dict[str, list[dict]] = {}
        self._file_qual: dict[tuple[str, str], str] = {}
        for row in conn.execute(
            "SELECT id, file_id, name, qualname, type, start_line, end_line, doc, tokens, "
            "parent, dup FROM symbols ORDER BY file_id, start_line"
        ):
            path = path_of.get(row[1])
            if path is None:
                continue
            sid = f"{path}:{row[3]}" + (f"@{row[5]}" if row[10] else "")
            sym = {
                "id": sid,
                "rowid": row[0],
                "file": path,
                "name": row[2],
                "qualname": row[3],
                "type": row[4],
                "start_line": row[5],
                "end_line": row[6],
                "doc": row[7] or "",
                "tokens": row[8],
                "parent": row[9],
            }
            self.by_id[sid] = sym
            self.by_rowid[row[0]] = sym
            self.by_name.setdefault(sym["name"], []).append(sym)
            self._file_qual.setdefault((path, sym["qualname"]), sid)
            self.files[path]["symbols"].append(sym)
        for sym in self.by_id.values():  # parent row id -> symbol id
            parent = self.by_rowid.get(sym["parent"]) if sym["parent"] else None
            sym["parent"] = parent["id"] if parent else None
        self.children: dict[str, list[str]] = {}
        for sym in self.by_id.values():
            if sym["parent"]:
                self.children.setdefault(sym["parent"], []).append(sym["id"])
        self._imports = {f: set(e["resolved_imports"]) for f, e in self.files.items()}
        self._importers: dict[str, set[str]] | None = None
        self._refs_cache: dict[str, list[str]] = {}
        self._name_files: dict[str, dict[str, list[dict]]] = {}
        self._nbr_cache: dict[str, set[str]] = {}
        self._stem_map: dict[str, list[str]] | None = None

    # ------------------------------------------------------------ files

    def importers(self, rel: str) -> set[str]:
        if self._importers is None:
            self._importers = {}
            for f, targets in self._imports.items():
                for t in targets:
                    self._importers.setdefault(t, set()).add(f)
        return self._importers.get(rel, set())

    def imports(self, rel: str) -> set[str]:
        return self._imports.get(rel, set())

    def test_targets(self, test_file: str) -> set[str]:
        """Source files a test file most likely exercises: its resolved
        imports plus the test_foo.py <-> foo.py filename convention,
        restricted to the same language family."""
        from pr_common import test_stem_guesses  # local: avoid import cycle

        if self._stem_map is None:
            self._stem_map = {}
            for f in self.files:
                p = PurePosixPath(f)
                self._stem_map.setdefault(p.stem, []).append(f)
        targets = set(self.imports(test_file))
        lang = self.files.get(test_file, {}).get("language")
        for stem in test_stem_guesses(test_file):
            for f in self._stem_map.get(stem, []):
                if f != test_file and self.files[f].get("language") == lang:
                    targets.add(f)
        return targets

    # ------------------------------------------------------------ references

    def refs(self, sid: str) -> list[str]:
        """Names symbol `sid` references (calls, annotations, signature types)."""
        if sid not in self._refs_cache:
            sym = self.by_id.get(sid)
            self._refs_cache[sid] = (
                [
                    r[0]
                    for r in self.conn.execute(
                        "SELECT name FROM edges WHERE src=?", (sym["rowid"],)
                    )
                ]
                if sym
                else []
            )
        return self._refs_cache[sid]

    def resolve(self, from_file: str, name: str) -> tuple[str | None, str]:
        """(target symbol id or None, precision) for a reference to `name`
        made from `from_file`."""
        same = self._file_qual.get((from_file, name))
        if same:
            return same, "same_file"
        cands = self.by_name.get(name, [])
        if not cands:
            return None, "unresolved"
        imported = self._imports.get(from_file, ())
        if len(cands) > 8 and imported:
            # common name: probe the (few) imported files instead of
            # scanning every same-named symbol repo-wide
            bucket = self._name_files.get(name)
            if bucket is None:
                bucket = self._name_files[name] = {}
                for c in cands:
                    bucket.setdefault(c["file"], []).append(c)
            in_imports = [c for f in imported for c in bucket.get(f, ())]
        else:
            in_imports = [c for c in cands if c["file"] in imported]
        if len(in_imports) == 1:
            return in_imports[0]["id"], "resolved_import"
        if len(cands) == 1:
            return cands[0]["id"], "unique_name_repo_wide"
        return None, "ambiguous"

    def callees(self, sid: str) -> set[str]:
        """Symbols `sid`'s own references resolve to."""
        sym = self.by_id.get(sid)
        out: set[str] = set()
        if sym is None:
            return out
        for name in self.refs(sid):
            target, _ = self.resolve(sym["file"], name)
            if target and target != sid:
                out.add(target)
        return out

    def neighbors(self, sid: str) -> set[str]:
        """Undirected call/reference neighbours: symbols `sid` resolves a
        reference to, symbols whose references resolve to `sid`, and the
        containment links class <-> its methods."""
        if sid in self._nbr_cache:
            return self._nbr_cache[sid]
        sym = self.by_id.get(sid)
        if sym is None:
            self._nbr_cache[sid] = set()
            return self._nbr_cache[sid]
        out = self.callees(sid)
        if sym["parent"]:
            out.add(sym["parent"])
        out.update(self.children.get(sid, ()))
        name = sym["name"]
        rows = self.conn.execute(
            "SELECT src FROM edges WHERE name=? LIMIT ?", (name, CALLER_FANOUT_CAP + 1)
        ).fetchall()
        if len(rows) > CALLER_FANOUT_CAP:
            near = {sym["file"]} | self.importers(sym["file"])
            rows = [
                r
                for r in self.conn.execute("SELECT src FROM edges WHERE name=?", (name,))
                if r[0] in self.by_rowid and self.by_rowid[r[0]]["file"] in near
            ]
        for (src_rowid,) in rows:
            src = self.by_rowid.get(src_rowid)
            if src and src["id"] != sid and self.resolve(src["file"], name)[0] == sid:
                out.add(src["id"])
        self._nbr_cache[sid] = out
        return out

    # ------------------------------------------------------------ stats

    def counts(self) -> dict:
        c = self.conn
        return {
            "files": c.execute("SELECT count(*) FROM files").fetchone()[0],
            "symbols": c.execute("SELECT count(*) FROM symbols").fetchone()[0],
            "edges": c.execute("SELECT count(*) FROM edges").fetchone()[0],
        }

"""Shared finding notes under <git root>/.mycelium/.

Layout:
  notes.json    {"version": 1, "notes": [note, ...]}  -- durable findings (commit it)
  spawns.jsonl  one line per gated Agent call, for duplicate detection (local)
  config.json   optional {"gate": "enforce"|"warn"|"off", "duplicate_threshold": 0.6}
  .lock         flock target; every read-modify-write holds it
  .gitignore    created once (never overwritten): ignores spawns.jsonl, .lock, *.tmp

A note stores a sha256 per path it describes; a path whose content changed
(or vanished) makes the note stale, so it is shown with a re-verify warning
instead of as fact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no flock; locking degrades to a no-op
    fcntl = None

STORE_DIRNAME = ".mycelium"
DEFAULT_CONFIG = {"gate": "enforce", "duplicate_threshold": 0.6}
GITIGNORE = "spawns.jsonl\n.lock\n*.tmp\n"
_WORD_RE = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "which", "where",
    "how", "does", "into", "are", "was", "not", "all", "any", "its", "use", "used",
    "task", "return", "budget", "why", "delegate", "known", "file", "files",
}


def find_repo_root() -> Path:
    """`git rev-parse --show-toplevel`, falling back to cwd outside a git repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except OSError:
        pass
    return Path.cwd().resolve()


def words(text: str) -> set:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def atomic_write_text(path: Path, text: str) -> None:
    """Temp file in the same dir + os.replace: readers never see a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def file_hash(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class Store:
    def __init__(self, repo_root=None):
        self.repo_root = Path(repo_root) if repo_root else find_repo_root()
        self.root = self.repo_root / STORE_DIRNAME

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with (self.root / ".gitignore").open("x") as f:  # "x": never overwrite
                f.write(GITIGNORE)
        except FileExistsError:
            pass

    @contextmanager
    def lock(self):
        """Exclusive cross-process lock over the store. Not reentrant."""
        self.ensure()
        with (self.root / ".lock").open("a") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------ config

    def config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        try:
            cfg.update(json.loads((self.root / "config.json").read_text()))
        except (OSError, ValueError):
            pass
        env = os.environ.get("MYCELIUM_GATE")
        if env in ("enforce", "warn", "off"):
            cfg["gate"] = env
        return cfg

    # ------------------------------------------------------------ notes

    def load_notes(self) -> list:
        try:
            return json.loads((self.root / "notes.json").read_text()).get("notes", [])
        except (OSError, ValueError):
            return []

    def _save_notes(self, notes: list) -> None:
        payload = json.dumps({"version": 1, "notes": notes}, indent=2) + "\n"
        atomic_write_text(self.root / "notes.json", payload)

    def _hashes(self, paths) -> dict:
        return {p: file_hash(self.repo_root / p) for p in paths}

    def add_note(self, topic: str, text: str, paths, kind: str = "finding") -> dict:
        """Add a note; an existing note with the same topic is replaced (one
        current answer per topic, not a growing log)."""
        rel = sorted({str(Path(p)) for p in paths})
        now = time.time()
        with self.lock():
            notes = self.load_notes()
            old = next((n for n in notes if n["topic"].lower() == topic.lower()), None)
            note = {
                "id": old["id"] if old else self._next_id(notes),
                "topic": topic,
                "kind": kind,
                "text": text,
                "paths": self._hashes(rel),
                "created_at": old["created_at"] if old else now,
                "verified_at": now,
            }
            notes = [n for n in notes if n is not old] + [note]
            self._save_notes(notes)
        return note

    @staticmethod
    def _next_id(notes: list) -> str:
        n = max((int(x["id"][1:]) for x in notes if x["id"][1:].isdigit()), default=0)
        return f"n{n + 1:03d}"

    def stale_paths(self, note: dict) -> list:
        return [p for p, h in note.get("paths", {}).items() if file_hash(self.repo_root / p) != h]

    def verify(self, note_id: str):
        """Re-hash a note's paths (after re-checking the finding) and mark it current."""
        with self.lock():
            notes = self.load_notes()
            note = next((n for n in notes if n["id"] == note_id), None)
            if note is None:
                return None
            note["paths"] = self._hashes(note["paths"])
            note["verified_at"] = time.time()
            self._save_notes(notes)
        return note

    def drop(self, note_id: str) -> bool:
        with self.lock():
            notes = self.load_notes()
            kept = [n for n in notes if n["id"] != note_id]
            if len(kept) == len(notes):
                return False
            self._save_notes(kept)
        return True

    def search(self, query: str, limit: int = 5, min_overlap: int = 2) -> list:
        """Notes sharing >= min_overlap content words (or a path) with query, best first."""
        q = words(query)
        scored = []
        for n in self.load_notes():
            if n.get("kind") == "status":
                continue  # task status is session-local, never briefed as fact
            hay = words(n["topic"] + " " + n["text"] + " " + " ".join(n["paths"]))
            overlap = len(q & hay) + sum(3 for p in n["paths"] if p in query)
            if overlap >= min_overlap:
                scored.append((overlap, n))
        scored.sort(key=lambda t: -t[0])
        return [n for _, n in scored[:limit]]

    # ------------------------------------------------------------ spawn log

    def spawns(self, session_id: str) -> list:
        out = []
        try:
            with (self.root / "spawns.jsonl").open() as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("session_id") == session_id:
                        out.append(rec)
        except OSError:
            pass
        return out

    def log_spawn(self, session_id: str, description: str, prompt: str) -> None:
        rec = {"session_id": session_id, "ts": time.time(), "description": description,
               "prompt": prompt[:4000]}
        with (self.root / "spawns.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")

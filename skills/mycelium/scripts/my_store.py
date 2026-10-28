"""Shared finding notes under <git root>/.mycelium/.

A session started in a folder of repos (not itself a git work tree) is a
workspace: each note is filed in the repo holding its files, and the gate
reads the stores of the repos an agent's brief names (`StoreSet`).

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
from contextlib import ExitStack, contextmanager
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
    except FileNotFoundError:  # no git binary
        pass
    return Path.cwd().resolve()


def git_root(start) -> Path | None:
    """Nearest ancestor of `start` (a file or dir) containing .git, or None."""
    d = Path(start).expanduser().resolve()
    for cand in (d, *d.parents):
        if (cand / ".git").exists():
            return cand
    return None


def workspace_repos(base: Path) -> list:
    """Child repos of a workspace folder."""
    return sorted(p for p in Path(base).iterdir() if (p / ".git").exists())


_PATHISH_RE = re.compile(r"[\w.~/-]+")


def repos_in_text(text: str, base: Path) -> list:
    """Repos a brief names, in order: by path (`repo/src/x.py`, absolute
    paths) or by a child repo's bare directory name."""
    base = Path(base).resolve()
    names = {p.name: p for p in workspace_repos(base)}
    found = []
    for tok in _PATHISH_RE.findall(text or ""):
        tok = tok.strip("./") if not tok.startswith(("/", "~")) else tok.rstrip(".")
        if not tok:
            continue
        if tok.startswith(("/", "~")):
            p = Path(tok).expanduser()
            while not p.exists() and p != p.parent:
                p = p.parent
            root = git_root(p) if p != p.parent else None
        else:
            head = tok.split("/", 1)[0]
            root = names.get(head) if "/" in tok or len(head) >= 3 else None
        if root is not None and root != base and root not in found:
            found.append(root)
    return found


def split_by_repo(paths, cwd: Path) -> dict:
    """{repo root: [repo-relative path, ...]} for paths given relative to
    `cwd` (or absolute); paths in no repo are dropped."""
    out = {}
    for raw in paths:
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else Path(cwd) / p
        root = git_root(p if p.is_dir() else p.parent)
        if root is None:
            continue
        try:
            rel = str(p.resolve().relative_to(root))
        except ValueError:  # resolves outside its repo (symlink)
            continue
        out.setdefault(root, []).append(rel)
    return out


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
        except FileNotFoundError:
            pass
        raise


def file_hash(path: Path):
    """sha256 of a file, or None once it is gone (the note is then stale)."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
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
        except FileNotFoundError:  # optional; a corrupt one raises
            pass
        env = os.environ.get("MYCELIUM_GATE")
        if env in ("enforce", "warn", "off"):
            cfg["gate"] = env
        return cfg

    # ------------------------------------------------------------ notes

    def load_notes(self) -> list:
        try:
            return json.loads((self.root / "notes.json").read_text()).get("notes", [])
        except FileNotFoundError:  # corrupt must raise: add_note would overwrite it
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
        """Notes sharing >= min_overlap content words (or a path) with query, best first.

        Words found in over a third of the notes (repo name, common dirs) carry no
        signal and are not counted, so they cannot drag in unrelated notes.
        """
        q = words(query)
        notes = [n for n in self.load_notes() if n.get("kind") != "status"]  # status is session-local
        hays = [words(n["topic"] + " " + n["text"] + " " + " ".join(n["paths"])) for n in notes]
        if len(hays) >= 4:
            q = {w for w in q if sum(w in h for h in hays) * 3 <= len(hays)}
        scored = []
        for n, hay in zip(notes, hays):
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
                    rec = json.loads(line)
                    if rec.get("session_id") == session_id:
                        out.append(rec)
        except FileNotFoundError:
            pass
        return out

    def log_spawn(self, session_id: str, description: str, prompt: str) -> None:
        rec = {"session_id": session_id, "ts": time.time(), "description": description,
               "prompt": prompt[:4000]}
        with (self.root / "spawns.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")


class StoreSet:
    """Several repos' stores seen as one by the gate and `brief`, for a
    workspace session. Note paths are shown relative to the workspace
    (`repo/src/x.py`); each note keeps its own store for staleness."""

    def __init__(self, stores, base: Path, spawn_stores=None):
        self.stores = list(stores)
        self.spawn_stores = list(self.stores if spawn_stores is None else spawn_stores)
        self.base = Path(base).resolve()

    def _label(self, store: Store, path: str) -> str:
        try:
            return str((store.repo_root / path).resolve().relative_to(self.base))
        except ValueError:  # resolves outside the workspace (symlink)
            return str(store.repo_root / path)

    def config(self) -> dict:
        return self.stores[0].config() if self.stores else dict(DEFAULT_CONFIG)

    @contextmanager
    def lock(self):
        with ExitStack() as stack:
            unique = {str(st.root): st for st in self.stores + self.spawn_stores}
            for key in sorted(unique):  # fixed order: no deadlock between sessions
                stack.enter_context(unique[key].lock())
            yield

    def spawns(self, session_id: str) -> list:
        return [rec for st in self.spawn_stores for rec in st.spawns(session_id)]

    def log_spawn(self, session_id: str, description: str, prompt: str) -> None:
        for st in self.spawn_stores:
            st.log_spawn(session_id, description, prompt)

    def load_notes(self) -> list:
        return [self._wrap(st, n) for st in self.stores for n in st.load_notes()]

    def search(self, query: str, limit: int = 5, min_overlap: int = 2) -> list:
        hits = [self._wrap(st, n) for st in self.stores for n in st.search(query, limit, min_overlap)]
        return hits[:limit]

    def _wrap(self, store: Store, note: dict) -> dict:
        return dict(note, id=f"{store.repo_root.name}/{note['id']}", _store=store, _orig=note,
                    paths={self._label(store, p): h for p, h in note.get("paths", {}).items()})

    def stale_paths(self, note: dict) -> list:
        store = note.get("_store")
        if store is None:
            return []
        return [self._label(store, p) for p in store.stale_paths(note["_orig"])]


def workspace_of(cwd) -> Path | None:
    """`cwd` itself when it is a folder of repos rather than inside one."""
    cwd = Path(cwd).resolve()
    return cwd if git_root(cwd) is None and workspace_repos(cwd) else None


def workspace_stores(base: Path, text: str) -> StoreSet:
    """The stores a workspace task reads: those of the repos `text` names;
    naming none, every repo that has notes (spawns then go to the
    workspace's own store, the only place left to detect duplicates)."""
    named = repos_in_text(text, base)
    if named:
        return StoreSet([Store(r) for r in named], base)
    with_notes = [Store(r) for r in workspace_repos(base) if (r / STORE_DIRNAME / "notes.json").is_file()]
    return StoreSet(with_notes, base, spawn_stores=[Store(base)])

"""The greenhouse base: one shared folder that links the repos of a platform.

A base is any directory holding a `greenhouse.json` marker. Layout:
  greenhouse.json   {"version": 1, "name": ..., "created_at": ...}
  repos/<slug>.json one card per repo: harvested facts + curated fields
  notes.json        {"version": 1, "notes": [...]} platform-wide notes
  INDEX.md          generated overview (repos, links, notes) for humans
  .lock             flock target; every read-modify-write holds it
  .gitignore        created once (never overwritten): ignores .lock, *.tmp

Discovery from a repo (first hit wins):
  1. --base / $GREENHOUSE_BASE
  2. <git root>/.greenhouse: a directory with the marker is the base itself
     (e.g. a submodule); a file holds a path (relative to the git root)
  3. <ancestor>/.greenhouse or <ancestor>/greenhouse holding the marker, for
     every ancestor of the git root (the usual "workspace of sibling repos")
"""

from __future__ import annotations

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

MARKER = "greenhouse.json"
POINTER = ".greenhouse"
ANCESTOR_DIRNAMES = (".greenhouse", "greenhouse")
GITIGNORE = ".lock\n*.tmp\n"
_WORD_RE = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "which", "where",
    "how", "does", "into", "are", "was", "not", "all", "any", "its", "use", "used",
    "repo", "repository", "code", "file", "files", "should", "when", "then", "than",
}


def git(args, cwd) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except FileNotFoundError:  # no git binary
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def find_repo_root(start=None) -> Path:
    """`git rev-parse --show-toplevel`, falling back to cwd outside a git repo."""
    top = git(["rev-parse", "--show-toplevel"], start or Path.cwd())
    return Path(top).resolve() if top else Path(start or Path.cwd()).resolve()


def _stem(word: str) -> str:
    """Crude plural folding so 'amounts' matches 'amount'."""
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def words(text: str) -> set:
    return {_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", name.strip().lower()).strip("-_.")
    return slug or "repo"


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def atomic_write_text(path: Path, text: str) -> None:
    """Temp file in the same dir + os.replace: readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)  # mkstemp's 0600 would hide the base from teammates
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _is_base(path: Path) -> bool:
    return (path / MARKER).is_file()


def discover(repo_root: Path, explicit=None):
    """(base path or None, how it was found)."""
    if explicit or os.environ.get("GREENHOUSE_BASE"):
        raw = explicit or os.environ["GREENHOUSE_BASE"]
        path = Path(raw).expanduser().resolve()
        return (path if _is_base(path) else None), ("--base" if explicit else "$GREENHOUSE_BASE")
    pointer = repo_root / POINTER
    if pointer.is_dir() and _is_base(pointer):
        return pointer.resolve(), f"{POINTER}/ in the repo"
    if pointer.is_file():
        target = pointer.read_text().strip().splitlines()
        if target:
            path = (repo_root / Path(target[0]).expanduser()).resolve()
            return (path if _is_base(path) else None), f"{POINTER} pointer file"
    for ancestor in repo_root.parents:
        for name in ANCESTOR_DIRNAMES:
            if _is_base(ancestor / name):
                return (ancestor / name).resolve(), f"ancestor {ancestor}"
    return None, "not found"


def default_location(repo_root: Path) -> Path:
    """Where `init`/`sync` create a base when none exists: next to the repo,
    so sibling repos in the same workspace find it by ancestor search."""
    return repo_root.parent / ".greenhouse"


class Base:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ------------------------------------------------------------ setup

    @classmethod
    def create(cls, root: Path, name=None) -> "Base":
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        (root / "repos").mkdir(exist_ok=True)
        try:
            with (root / MARKER).open("x") as f:  # "x": never overwrite an existing base
                f.write(json.dumps({"version": 1, "name": name or root.parent.name,
                                    "created_at": time.time()}, indent=2) + "\n")
        except FileExistsError:
            pass
        try:
            with (root / ".gitignore").open("x") as f:
                f.write(GITIGNORE)
        except FileExistsError:
            pass
        base = cls(root)
        if not (root / "INDEX.md").exists():
            base.render_index()
        return base

    def meta(self) -> dict:
        try:
            return json.loads((self.root / MARKER).read_text())
        except FileNotFoundError:
            return {}

    def set_meta(self, key: str, value) -> None:
        with self.lock():
            meta = self.meta()
            meta[key] = value
            atomic_write_text(self.root / MARKER, json.dumps(meta, indent=2) + "\n")

    @contextmanager
    def lock(self):
        """Exclusive cross-process lock over the base. Not reentrant."""
        with (self.root / ".lock").open("a") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------ cards

    def card_path(self, slug: str) -> Path:
        return self.root / "repos" / f"{slugify(slug)}.json"

    def load_card(self, slug: str):
        try:
            return json.loads(self.card_path(slug).read_text())
        except FileNotFoundError:
            return None

    def cards(self) -> list:
        out = []
        for path in sorted((self.root / "repos").glob("*.json")):
            try:
                out.append(json.loads(path.read_text()))
            except FileNotFoundError:  # forgotten since the glob
                continue
        return out

    def save_card(self, harvested: dict, curated: dict) -> dict:
        """Replace the harvested part of a card; curated fields (summary,
        provides, consumes, owners) are only changed when given, never
        cleared by a re-harvest."""
        with self.lock():
            old = self.load_card(harvested["slug"]) or {}
            keep = dict(old.get("curated") or {})
            for key, value in curated.items():
                if value is None:
                    continue
                if isinstance(value, list):
                    merged = list(keep.get(key) or [])
                    merged += [v for v in value if v not in merged]
                    keep[key] = merged
                else:
                    keep[key] = value
            card = dict(harvested, curated=keep, created_at=old.get("created_at", time.time()))
            atomic_write_text(self.card_path(card["slug"]), json.dumps(card, indent=2) + "\n")
            self._render_index_locked()
        return card

    def forget(self, slug: str) -> bool:
        with self.lock():
            path = self.card_path(slug)
            if not path.exists():
                return False
            path.unlink()
            self._render_index_locked()
        return True

    # ------------------------------------------------------------ notes

    def notes(self) -> list:
        try:
            return json.loads((self.root / "notes.json").read_text()).get("notes", [])
        except FileNotFoundError:
            return []

    def _save_notes(self, notes: list) -> None:
        atomic_write_text(self.root / "notes.json",
                          json.dumps({"version": 1, "notes": notes}, indent=2) + "\n")

    def add_note(self, topic: str, text: str, repos, author) -> dict:
        """One current note per topic: the same topic replaces the old text."""
        with self.lock():
            notes = self.notes()
            old = next((n for n in notes if n["topic"].lower() == topic.lower()), None)
            n = max((int(x["id"][1:]) for x in notes if x["id"][1:].isdigit()), default=0)
            note = {
                "id": old["id"] if old else f"p{n + 1:03d}",
                "topic": topic,
                "text": text,
                "repos": sorted({slugify(r) for r in repos}),
                "author_repo": author,
                "created_at": old["created_at"] if old else time.time(),
                "updated_at": time.time(),
            }
            self._save_notes([x for x in notes if x is not old] + [note])
            self._render_index_locked()
        return note

    def drop_note(self, note_id: str) -> bool:
        with self.lock():
            notes = self.notes()
            kept = [n for n in notes if n["id"] != note_id]
            if len(kept) == len(notes):
                return False
            self._save_notes(kept)
            self._render_index_locked()
        return True

    # ------------------------------------------------------------ links

    def links(self, cards=None) -> list:
        """(from_slug, to_slug, why) edges: a dependency on a package another
        card provides, or a curated `consumes` naming another repo."""
        cards = self.cards() if cards is None else cards
        by_pkg = {}
        slugs = {c["slug"] for c in cards}
        for c in cards:
            for pkg in c.get("packages", []):
                by_pkg.setdefault(_norm_pkg(pkg), c["slug"])
        edges = set()
        for c in cards:
            for dep in c.get("dependencies", []):
                target = by_pkg.get(_norm_pkg(dep))
                if target and target != c["slug"]:
                    edges.add((c["slug"], target, f"depends on {dep}"))
            for item in (c.get("curated") or {}).get("consumes", []):
                target = slugify(item.split(":", 1)[0])
                if target in slugs and target != c["slug"]:
                    edges.add((c["slug"], target, f"consumes {item}"))
        return sorted(edges)

    # ------------------------------------------------------------ index

    def render_index(self) -> None:
        with self.lock():
            self._render_index_locked()

    def _render_index_locked(self) -> None:
        cards = self.cards()
        lines = [f"# Greenhouse: {self.meta().get('name', self.root.parent.name)}", "",
                 "<!-- generated by greenhouse; edit cards/notes with the CLI -->", "", "## Repos", ""]
        for c in cards:
            lines.append(f"- **{c['slug']}** ({', '.join(c.get('languages', [])[:3]) or '?'}): "
                         f"{summary_of(c) or '(no summary)'}")
        if not cards:
            lines.append("- (none yet: run `greenhouse sync` in a repo)")
        edges = self.links(cards)
        if edges:
            lines += ["", "## Links", ""] + [f"- {a} -> {b} ({why})" for a, b, why in edges]
        notes = self.notes()
        if notes:
            lines += ["", "## Platform notes", ""]
            for n in notes:
                scope = f" [{', '.join(n['repos'])}]" if n.get("repos") else ""
                lines.append(f"- [{n['id']}] **{n['topic']}**{scope}: {n['text']}")
        atomic_write_text(self.root / "INDEX.md", "\n".join(lines) + "\n")


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def summary_of(card: dict) -> str:
    return (card.get("curated") or {}).get("summary") or card.get("readme_summary", "")


def head_of(path: Path) -> str:
    return git(["rev-parse", "HEAD"], path)


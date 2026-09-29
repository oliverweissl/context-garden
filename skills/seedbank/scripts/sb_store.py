"""Local storage for seedbank. Layout under the store root (.seedbank/ at the git root):

    observations.jsonl  append-only raw event log; `gc` may prune it (keystats holds the aggregates)
    keystats.json       {key: aggregate stats}, key = file path, search, command, or fact/mistake digest
    facts.json          {seq, facts: {fact_id: {...}}}: promoted hot/warm facts
    config.json         overrides of CONFIG_DEFAULTS (missing keys fall back)
    hook.log            errors swallowed by the Claude Code hook
    .lock               flock target serializing concurrent commands
    .gitignore          written once at creation; ignores the above, not warm/*.md
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

from sb_scoring import DEFAULT_FACT_FAILURE_COST, DEFAULT_HALF_LIFE_DAYS

try:
    import fcntl
except ImportError:  # Windows: no flock; locking degrades to a no-op
    fcntl = None

DEFAULT_STORE_DIRNAME = ".seedbank"
# Written into a freshly created store: the profiler state is local, while
# warm/*.md (and AGENTS.md outside the store) are meant to be committed.
STORE_GITIGNORE = """\
# seedbank local profiler state (warm/*.md is meant to be committed)
observations.jsonl
keystats.json
facts.json
config.json
.lock
hook.log
hook.log.1
*.tmp
"""
DEFAULT_HOT_BUDGET = 500
DEFAULT_PROMOTE_THRESHOLD = 1.0

FAILURE_COST_RUN = 150
FAILURE_COST_MISTAKE = 300
SEARCH_DEFAULT_COST = 30

CONFIG_DEFAULTS = {
    "hot_budget": DEFAULT_HOT_BUDGET,
    "promote_threshold": DEFAULT_PROMOTE_THRESHOLD,
    "half_life_days": DEFAULT_HALF_LIFE_DAYS,
    "fact_failure_cost": DEFAULT_FACT_FAILURE_COST,
}

SESSION_ENV_VARS = ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")


def resolve_session(explicit: str | None = None) -> str:
    """Session id used to count *distinct sessions* per key: explicit
    `--session`, else the id Claude Code exports to Bash tool calls
    (CLAUDE_CODE_SESSION_ID; CLAUDE_SESSION_ID also honoured), else a
    fallback of today's date + the OS session id of this terminal (stable
    across the commands of one shell session; ppid if getsid is missing)."""
    if explicit:
        return explicit
    for var in SESSION_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    try:
        sid = os.getsid(0)
    except (AttributeError, OSError):
        sid = os.getppid()
    return f"{time.strftime('%Y-%m-%d')}-{sid}"


def find_repo_root() -> Path:
    """`git rev-parse --show-toplevel`, falling back to cwd outside a git repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except OSError:
        pass
    return Path.cwd().resolve()


def find_store_root(explicit: str | None, repo_root: Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return (repo_root or find_repo_root()) / DEFAULT_STORE_DIRNAME


def rel_to_root(path: str, repo_root: Path) -> str:
    """Normalize a user-supplied path to repo-root-relative (posix) form so
    keys/sources don't depend on the cwd the command was run from. Paths
    outside the repo are kept absolute."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    try:
        return p.relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return str(p)


def safe_scope(scope: str | None) -> str:
    """Reduce a scope to a filename-safe slug (it becomes warm/<scope>.md)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", (scope or "").strip().lower()).strip("-_")
    return slug or "general"


def atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file in the same dir + os.replace, so readers never
    see a half-written file."""
    path = Path(path)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def estimate_tokens(text: str) -> int:
    """Chars/4 rule-of-thumb token estimate. No tokenizer dependency by design."""
    return max(1, round(len(text) / 4))


def sha256_file(path: str) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:16]


class Store:
    def __init__(self, root: Path, repo_root: Path | None = None):
        self.root = Path(root)
        self.repo_root = Path(repo_root) if repo_root else self.root.resolve().parent
        self.session: str | None = None  # set by the CLI / hook (resolve_session)
        created = not self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        if created:
            try:  # "x": never overwrite a .gitignore someone else put there
                with (self.root / ".gitignore").open("x") as f:
                    f.write(STORE_GITIGNORE)
            except FileExistsError:
                pass
        self.lock_path = self.root / ".lock"
        self.obs_path = self.root / "observations.jsonl"
        self.keystats_path = self.root / "keystats.json"
        self.facts_path = self.root / "facts.json"
        self.config_path = self.root / "config.json"
        with self.lock():
            if not self.keystats_path.exists():
                atomic_write_text(self.keystats_path, "{}")
            if not self.facts_path.exists():
                atomic_write_text(self.facts_path, json.dumps({"seq": 0, "facts": {}}, indent=2))
            if not self.config_path.exists():
                atomic_write_text(self.config_path, json.dumps(CONFIG_DEFAULTS, indent=2))
            self.obs_path.touch(exist_ok=True)

    @contextmanager
    def lock(self):
        """Exclusive cross-process lock; hold it across a whole
        load -> modify -> save sequence. Not reentrant."""
        with self.lock_path.open("a") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def source_path(self, source: str) -> Path:
        """Resolve a stored (repo-root-relative or absolute) source path."""
        return self.repo_root / source

    # -- keystats -----------------------------------------------------
    def load_keystats(self) -> dict:
        return json.loads(self.keystats_path.read_text())

    def save_keystats(self, data: dict) -> None:
        atomic_write_text(self.keystats_path, json.dumps(data, indent=2))

    # -- facts ----------------------------------------------------------
    def load_facts(self) -> dict:
        return json.loads(self.facts_path.read_text())

    def save_facts(self, data: dict) -> None:
        atomic_write_text(self.facts_path, json.dumps(data, indent=2))

    @staticmethod
    def gen_fact_id(facts: dict) -> str:
        """Mutates `facts["seq"]` in place; caller must save the same dict
        afterward so the increment and the new fact land in one write."""
        facts["seq"] += 1
        return f"f{facts['seq']:04d}"

    # -- config -----------------------------------------------------------
    def load_config(self) -> dict:
        """Stored overrides on top of CONFIG_DEFAULTS, so a config.json
        written by an older version (no half_life_days etc.) still loads."""
        return {**CONFIG_DEFAULTS, **json.loads(self.config_path.read_text())}

    def save_config(self, data: dict) -> None:
        atomic_write_text(self.config_path, json.dumps(data, indent=2))

    # -- observations -----------------------------------------------------
    def append_observation(self, obs: dict) -> None:
        with self.obs_path.open("a") as f:
            f.write(json.dumps(obs) + "\n")

    def read_observations(self) -> list[dict]:
        out = []
        for line in self.obs_path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def prune_observations(self, keep_per_key: int = 5) -> int:
        """Keep only the last `keep_per_key` raw observations per key.

        Safe because keystats.json already holds the durable aggregates;
        the raw log is only needed as an audit trail for recent activity.
        Returns the number of entries removed.
        """
        by_key: dict[str, list[dict]] = {}
        for o in self.read_observations():
            by_key.setdefault(o["key"], []).append(o)
        kept = []
        removed = 0
        for key, obs_list in by_key.items():
            removed += max(0, len(obs_list) - keep_per_key)
            kept.extend(obs_list[-keep_per_key:])
        kept.sort(key=lambda o: o["ts"])
        atomic_write_text(self.obs_path, "".join(json.dumps(o) + "\n" for o in kept))
        return removed


def now() -> float:
    return time.time()

"""Local, offline storage for compost runs.

Layout under the store root (default <git root, else cwd>/.compost):

    index.json              global run counter + command-signature -> run_id history
    config.json             optional: {"keep_per_command": 10, "max_store_mb": 200}
    runs/<run_id>/raw.txt   verbatim captured output (streamed to disk, never mutated)
    runs/<run_id>/meta.json parsed summary + internal event index for this run

Retention (`Store.gc`, run after every run/ingest and by `compost gc`) keeps
the last `keep_per_command` runs per command and evicts oldest-first while the
runs exceed `max_store_mb`, never touching the run just created, the latest
run of any command, or a run still in progress.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX: best effort, no locking
    fcntl = None

DEFAULT_STORE_DIRNAME = ".compost"
DEFAULT_CONFIG = {"keep_per_command": 10, "max_store_mb": 200}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise FileNotFoundError(f"invalid run id {run_id!r}")
    return run_id


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


def find_store_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return find_repo_root() / DEFAULT_STORE_DIRNAME


def command_key(command: str) -> str:
    """Normalized command string: runs of whitespace collapsed."""
    return " ".join((command or "").split())


def command_signature(command: str) -> str:
    return hashlib.sha1(command_key(command).encode("utf-8", errors="replace")).hexdigest()[:12]


def _run_seq(run_id: str) -> int:
    m = re.match(r"^r(\d+)$", run_id)
    return int(m.group(1)) if m else -1


def iter_raw_lines(path: Path):
    """Yield the stored output line by line (newline-split, trailing \\n /
    \\r\\n removed, decoded with errors='replace'): the single definition of
    "line N" shared by parsing, get, event and grep. Memory stays flat."""
    with open(path, "rb") as fh:
        for raw in fh:
            if raw.endswith(b"\n"):
                raw = raw[:-1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
            yield raw.decode("utf-8", errors="replace")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _dir_size(path: Path) -> int:
    total = 0
    with contextlib.suppress(OSError):
        for entry in os.scandir(path):
            with contextlib.suppress(OSError):
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / "index.lock"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not self.index_path.exists():
                self._write_index({"seq": 0, "by_signature": {}})

    @contextlib.contextmanager
    def _locked(self):
        """Exclusive inter-process lock around index read-modify-write."""
        with open(self.lock_path, "a") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_index(self) -> dict:
        return json.loads(self.index_path.read_text())

    def _write_index(self, data: dict) -> None:
        _atomic_write(self.index_path, json.dumps(data, indent=2))

    def config(self) -> dict:
        """DEFAULT_CONFIG overlaid with <store>/config.json (if present/valid)."""
        cfg = dict(DEFAULT_CONFIG)
        path = self.root / "config.json"
        with contextlib.suppress(OSError, ValueError, TypeError):
            data = json.loads(path.read_text())
            for key in DEFAULT_CONFIG:
                if isinstance(data.get(key), (int, float)) and not isinstance(data[key], bool):
                    cfg[key] = data[key]
        return cfg

    def next_run_id(self, command: str | None = None) -> str:
        """Allocate a fresh run id and create its directory (never reused).
        A provisional `status: running` meta.json marks it in progress."""
        with self._locked():
            idx = self._read_index()
            while True:
                idx["seq"] += 1
                run_id = f"r{idx['seq']:04d}"
                try:
                    (self.runs_dir / run_id).mkdir(exist_ok=False)
                    break
                except FileExistsError:
                    continue
            self._write_index(idx)
            self.save_meta(
                run_id,
                {
                    "raw_output_id": run_id,
                    "command": command,
                    "status": "running",
                    "exit_code": None,
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                },
            )
        return run_id

    def previous_run_for(self, command: str) -> str | None:
        with self._locked():
            idx = self._read_index()
        sig = command_signature(command)
        runs = idx["by_signature"].get(sig, [])
        return runs[-1] if runs else None

    def record_signature(self, command: str, run_id: str) -> None:
        with self._locked():
            idx = self._read_index()
            sig = command_signature(command)
            idx["by_signature"].setdefault(sig, []).append(run_id)
            self._write_index(idx)

    def run_dir(self, run_id: str) -> Path:
        """Path of an existing-or-allocated run dir; never creates directories."""
        return self.runs_dir / validate_run_id(run_id)

    def raw_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "raw.txt"

    def iter_raw(self, run_id: str):
        path = self.raw_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"no raw output stored for run {run_id}")
        return iter_raw_lines(path)

    def save_meta(self, run_id: str, meta: dict) -> None:
        _atomic_write(self.run_dir(run_id) / "meta.json", json.dumps(meta, indent=2))

    def load_meta(self, run_id: str) -> dict:
        path = self.run_dir(run_id) / "meta.json"
        if not path.exists():
            raise FileNotFoundError(f"no metadata stored for run {run_id}")
        return json.loads(path.read_text())

    def _run_dirs(self) -> list:
        """Run directories, oldest first (numeric run-id order)."""
        dirs = [d for d in self.runs_dir.iterdir() if d.is_dir() and _run_seq(d.name) >= 0]
        return sorted(dirs, key=lambda d: _run_seq(d.name))

    def _evictable(self, run_id: str) -> bool:
        """Finished runs, and abandoned ones (status running, owner pid gone)."""
        try:
            meta = json.loads((self.runs_dir / run_id / "meta.json").read_text())
        except (OSError, ValueError):
            return False  # being created right now, or not ours to judge
        return meta.get("status") != "running" or not _pid_alive(meta.get("pid"))

    def gc(
        self,
        keep_per_command: int | None = None,
        max_store_mb: float | None = None,
        protect=(),
    ) -> dict:
        """Apply retention under the store lock (see module docstring)."""
        cfg = self.config()
        keep = int(keep_per_command if keep_per_command is not None else cfg["keep_per_command"])
        keep = max(1, keep)
        max_mb = float(max_store_mb if max_store_mb is not None else cfg["max_store_mb"])
        max_bytes = int(max_mb * 1024 * 1024)
        evicted = []
        with self._locked():
            idx = self._read_index()
            by_sig = idx["by_signature"]
            protected = set(protect) | {runs[-1] for runs in by_sig.values() if runs}

            def evict(run_id: str) -> bool:
                if run_id in protected or not self._evictable(run_id):
                    return False
                shutil.rmtree(self.runs_dir / run_id, ignore_errors=True)
                evicted.append(run_id)
                return True

            for sig, runs in by_sig.items():
                for run_id in runs[:-keep]:
                    evict(run_id)

            dirs = self._run_dirs()
            sizes = {d.name: _dir_size(d) for d in dirs}
            before = total = sum(sizes.values())
            total -= sum(sizes.get(r, 0) for r in evicted)
            for d in dirs:
                if total <= max_bytes:
                    break
                if d.name not in evicted and evict(d.name):
                    total -= sizes[d.name]

            gone = set(evicted)
            for sig in list(by_sig):
                by_sig[sig] = [r for r in by_sig[sig] if r not in gone]
                if not by_sig[sig]:
                    del by_sig[sig]
            self._write_index(idx)
        return {
            "evicted": evicted,
            "keep_per_command": keep,
            "max_store_mb": max_mb,
            "bytes_before": before,
            "bytes_after": total,
        }

    def list_runs(self, limit: int = 20) -> list[dict]:
        runs = []
        for d in reversed(self._run_dirs()):
            meta_path = d / "meta.json"
            if meta_path.exists():
                runs.append(json.loads(meta_path.read_text()))
            if len(runs) >= limit:
                break
        return runs

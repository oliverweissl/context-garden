"""Local, offline storage for compost runs.

Layout under the store root (default ./.compost):

    index.json              global run counter + command-signature -> run_id history
    runs/<run_id>/raw.txt   verbatim captured output (never mutated, never discarded)
    runs/<run_id>/meta.json parsed summary + internal event index for this run
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

DEFAULT_STORE_DIRNAME = ".compost"


def find_store_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path.cwd() / DEFAULT_STORE_DIRNAME


def command_signature(command: str) -> str:
    return hashlib.sha1(command.encode("utf-8", errors="replace")).hexdigest()[:12]


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.json"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index({"seq": 0, "by_signature": {}})

    def _read_index(self) -> dict:
        return json.loads(self.index_path.read_text())

    def _write_index(self, data: dict) -> None:
        self.index_path.write_text(json.dumps(data, indent=2))

    def next_run_id(self) -> str:
        idx = self._read_index()
        idx["seq"] += 1
        run_id = f"r{idx['seq']:04d}"
        self._write_index(idx)
        return run_id

    def previous_run_for(self, command: str) -> str | None:
        idx = self._read_index()
        sig = command_signature(command)
        runs = idx["by_signature"].get(sig, [])
        return runs[-1] if runs else None

    def record_signature(self, command: str, run_id: str) -> None:
        idx = self._read_index()
        sig = command_signature(command)
        idx["by_signature"].setdefault(sig, []).append(run_id)
        self._write_index(idx)

    def run_dir(self, run_id: str) -> Path:
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_raw(self, run_id: str, text: str) -> Path:
        path = self.run_dir(run_id) / "raw.txt"
        path.write_text(text, errors="replace")
        return path

    def load_raw(self, run_id: str) -> str:
        path = self.run_dir(run_id) / "raw.txt"
        if not path.exists():
            raise FileNotFoundError(f"no raw output stored for run {run_id}")
        return path.read_text(errors="replace")

    def save_meta(self, run_id: str, meta: dict) -> None:
        (self.run_dir(run_id) / "meta.json").write_text(json.dumps(meta, indent=2))

    def load_meta(self, run_id: str) -> dict:
        path = self.run_dir(run_id) / "meta.json"
        if not path.exists():
            raise FileNotFoundError(f"no metadata stored for run {run_id}")
        return json.loads(path.read_text())

    def list_runs(self, limit: int = 20) -> list[dict]:
        runs = []
        for d in sorted(self.runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
            meta_path = d / "meta.json"
            if meta_path.exists():
                runs.append(json.loads(meta_path.read_text()))
            if len(runs) >= limit:
                break
        return runs

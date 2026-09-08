"""Local, offline storage for seedbank.

Layout under the store root (default ./.seedbank):

    observations.jsonl   append-only raw event log (audit trail; safe to
                          prune with `gc` since keystats.json already holds
                          the running aggregates observe updates incrementally)
    keystats.json         {key: aggregate stats} -- one entry per distinct
                          repeated-access "key" (a file path, search pattern,
                          command, or an agent-declared fact/mistake digest)
    facts.json             {seq, facts: {fact_id: {...}}} -- promoted,
                          persistent facts (hot/warm tier)
    config.json            {hot_budget, promote_threshold} overrides
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

DEFAULT_STORE_DIRNAME = ".seedbank"
DEFAULT_HOT_BUDGET = 500
DEFAULT_PROMOTE_THRESHOLD = 1.0

FAILURE_COST_RUN = 150
FAILURE_COST_MISTAKE = 300
SEARCH_DEFAULT_COST = 30


def find_store_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path.cwd() / DEFAULT_STORE_DIRNAME


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
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.obs_path = self.root / "observations.jsonl"
        self.keystats_path = self.root / "keystats.json"
        self.facts_path = self.root / "facts.json"
        self.config_path = self.root / "config.json"
        if not self.keystats_path.exists():
            self.keystats_path.write_text("{}")
        if not self.facts_path.exists():
            self.facts_path.write_text(json.dumps({"seq": 0, "facts": {}}, indent=2))
        if not self.config_path.exists():
            self.config_path.write_text(
                json.dumps(
                    {
                        "hot_budget": DEFAULT_HOT_BUDGET,
                        "promote_threshold": DEFAULT_PROMOTE_THRESHOLD,
                    },
                    indent=2,
                )
            )
        self.obs_path.touch(exist_ok=True)

    # -- keystats -----------------------------------------------------
    def load_keystats(self) -> dict:
        return json.loads(self.keystats_path.read_text())

    def save_keystats(self, data: dict) -> None:
        self.keystats_path.write_text(json.dumps(data, indent=2))

    # -- facts ----------------------------------------------------------
    def load_facts(self) -> dict:
        return json.loads(self.facts_path.read_text())

    def save_facts(self, data: dict) -> None:
        self.facts_path.write_text(json.dumps(data, indent=2))

    @staticmethod
    def gen_fact_id(facts: dict) -> str:
        """Mutates `facts["seq"]` in place; caller must save the same dict
        afterward so the increment and the new fact land in one write."""
        facts["seq"] += 1
        return f"f{facts['seq']:04d}"

    # -- config -----------------------------------------------------------
    def load_config(self) -> dict:
        return json.loads(self.config_path.read_text())

    def save_config(self, data: dict) -> None:
        self.config_path.write_text(json.dumps(data, indent=2))

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
        with self.obs_path.open("w") as f:
            for o in kept:
                f.write(json.dumps(o) + "\n")
        return removed


def now() -> float:
    return time.time()

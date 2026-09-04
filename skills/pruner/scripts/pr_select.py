"""Budgeted chunk selection ("the knapsack step") + slice persistence for
progressive expansion.

Selection is a simple greedy fill in descending score order, not an exact
knapsack solve -- the spec asks to "solve approximately", and a plain
score-ordered greedy is far easier to explain ("this was included because
it scored higher than the budget cutoff") than a density-optimized packing
would be, which matters since every chunk's inclusion has to be justified
in `reasons`. See references/scoring.md for the tradeoff.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pr_common import estimate_tokens, read_text
from pr_graph import all_distances, bfs_distances, build_adjacency, file_of, merge_external_graph
from pr_score import extract_keywords, graph_score, lexical_match, resolve_error_locations

REQUIRED_THRESHOLD = 8.0
EXACT_SEED_THRESHOLD = 9.0
CONFIG_PRIORITY_NAMES = {"cmakelists.txt", "pyproject.toml", "setup.py", "makefile", "package.json"}
OMITTED_CAP = 30
SLICES_DIRNAME = "slices"


def _all_chunks(index: dict) -> list[dict]:
    """One chunk per symbol, plus one whole-file chunk for files with no
    symbols at all (config/doc/unparsed files, or empty source files)."""
    chunks = []
    for rel, entry in index["files"].items():
        if entry["symbols"]:
            for sym in entry["symbols"]:
                chunks.append({
                    "id": sym["id"], "file": rel, "start_line": sym["start_line"],
                    "end_line": sym["end_line"], "name": sym["name"], "doc": sym.get("doc", ""),
                    "chunk_kind": sym["type"], "file_kind": entry["kind"],
                })
        else:
            chunks.append({
                "id": f"{rel}:__file__", "file": rel, "start_line": 1,
                "end_line": max(1, entry["line_count"]), "name": Path(rel).name, "doc": "",
                "chunk_kind": "file", "file_kind": entry["kind"],
            })
    return chunks


def _chunk_tokens(repo_root: Path, chunk: dict) -> int:
    text = read_text(repo_root / chunk["file"])
    if text is None:
        return 1
    lines = text.splitlines()
    snippet = "\n".join(lines[chunk["start_line"] - 1: chunk["end_line"]])
    return estimate_tokens(snippet)


def score_all(index: dict, repo_root: Path, task: str, changed_files: list[str] | None,
              error_text: str | None, external_graph: dict | None) -> list[dict]:
    task_lower = (task or "").lower()
    task_keywords = extract_keywords(task or "")
    chunks = _all_chunks(index)
    changed_set = set(changed_files or [])
    error_seed_ids = set(resolve_error_locations(error_text, index)) if error_text else set()

    lexical = {}
    for c in chunks:
        s, reasons = lexical_match(c["name"], c["doc"], c["file"], task_lower, task_keywords)
        lexical[c["id"]] = (s, reasons)

    seeds = {cid for cid, (s, _) in lexical.items() if s >= EXACT_SEED_THRESHOLD}
    seeds |= error_seed_ids
    if not seeds:
        seeds = {c["id"] for c in chunks if c["file"] in changed_set}

    if seeds:
        if external_graph:
            adj = build_adjacency(index["call_edges"])
            merge_external_graph(adj, external_graph)
            dist = bfs_distances(adj, seeds)
        else:
            dist = all_distances(index, seeds)
    else:
        dist = {}

    seed_files = {file_of(s) for s in seeds}
    scored = []
    for c in chunks:
        lex_score, lex_reasons = lexical[c["id"]]
        reasons = list(lex_reasons)
        g_score, g_reason = graph_score(dist.get(c["id"]))
        if g_reason:
            reasons.append(g_reason)

        test_boost = 0.0
        if c["file_kind"] == "test":
            tested = {f for f, tests in index["test_links"].items() if c["file"] in tests}
            if tested & seed_files:
                test_boost = 8.0
                reasons.append("tests a file the task/error/diff implicates")

        recency_boost = 0.0
        if c["file"] in changed_set:
            recency_boost = 6.0
            reasons.append("file was recently changed")

        error_boost = 0.0
        if c["id"] in error_seed_ids:
            error_boost = 15.0
            reasons.append("encloses a location named in the provided error/traceback")

        total = lex_score + g_score + test_boost + recency_boost + error_boost
        scored.append({**c, "score": total, "reasons": reasons, "is_seed": c["id"] in seeds})

    for c in scored:
        if c["file_kind"] == "config" and c["score"] <= 0 and Path(c["file"]).name.lower() in CONFIG_PRIORITY_NAMES:
            c["score"] = 0.5
            c["reasons"].append("primary project configuration file (always considered)")

    scored.sort(key=lambda c: -c["score"])
    return scored


def select(index: dict, repo_root: Path, task: str, budget: int, changed_files: list[str] | None = None,
           error_text: str | None = None, external_graph: dict | None = None) -> dict:
    scored = score_all(index, repo_root, task, changed_files, error_text, external_graph)
    for c in scored:
        c["tokens"] = _chunk_tokens(repo_root, c)

    used = 0
    required, supporting, tests, config, omitted = [], [], [], [], []
    for c in scored:
        if c["score"] <= 0:
            omitted.append(c)
            continue
        if used + c["tokens"] > budget:
            omitted.append(c)
            continue
        used += c["tokens"]
        if c["file_kind"] == "test":
            tests.append(c)
        elif c["file_kind"] == "config":
            config.append(c)
        elif c["score"] >= REQUIRED_THRESHOLD:
            required.append(c)
        else:
            supporting.append(c)

    omitted.sort(key=lambda c: -c["score"])
    confidence = "high" if (required or tests) else ("medium" if supporting else "low")

    return {
        "task": task,
        "budget": budget,
        "used_tokens": used,
        "required_context": required,
        "supporting_context": supporting,
        "relevant_tests": tests,
        "relevant_config": config,
        "omitted_candidates": omitted[:OMITTED_CAP],
        "confidence": confidence,
    }


# ---------------------------------------------------------------- slice persistence

def _slice_dir(store_dir: Path) -> Path:
    d = store_dir / SLICES_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_slice_id(store_dir: Path) -> str:
    d = _slice_dir(store_dir)
    existing = [p.stem for p in d.glob("s*.json")]
    n = 0
    for e in existing:
        try:
            n = max(n, int(e[1:]))
        except ValueError:
            pass
    return f"s{n + 1:04d}"


def save_slice(store_dir: Path, slice_id: str, repo_root: Path, result: dict) -> Path:
    record = {"slice_id": slice_id, "repo_root": str(repo_root), "created_at": time.time(), **result}
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def load_slice(store_dir: Path, slice_id: str) -> dict:
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no slice {slice_id}")
    return json.loads(path.read_text())


def expand_slice(store_dir: Path, slice_id: str, add_chunk_ids: list[str],
                  extra_budget: int | None = None) -> dict:
    """Promote specific omitted candidates (by chunk id) into the slice's
    supporting_context, without rerunning the whole scoring pass -- this is
    the "identify the missing dependency, expand just that" step, not a
    full reselect."""
    record = load_slice(store_dir, slice_id)
    budget = record["budget"] + (extra_budget or 0)
    omitted_by_id = {c["id"]: c for c in record["omitted_candidates"]}
    promoted, still_missing, over_budget = [], [], []

    for cid in add_chunk_ids:
        chunk = omitted_by_id.get(cid)
        if chunk is None:
            still_missing.append(cid)
            continue
        if record["used_tokens"] + chunk["tokens"] > budget:
            over_budget.append(cid)
            continue
        chunk["reasons"].append("manually expanded: agent identified this as a missing dependency")
        record["supporting_context"].append(chunk)
        record["used_tokens"] += chunk["tokens"]
        record["omitted_candidates"] = [c for c in record["omitted_candidates"] if c["id"] != cid]
        promoted.append(cid)

    record["budget"] = budget
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return {"record": record, "promoted": promoted, "still_missing": still_missing, "over_budget": over_budget}


def add_ad_hoc_chunk(store_dir: Path, slice_id: str, repo_root: Path, file: str, start_line: int,
                      end_line: int, reason: str, extra_budget: int | None = None) -> dict:
    """Inject a specific file:line range the scorer never surfaced at all
    (e.g. a doc file, or a range inside a huge unparsed file). Still
    budget-enforced, like expand_slice -- pass extra_budget to raise the
    ceiling deliberately rather than silently exceeding it."""
    record = load_slice(store_dir, slice_id)
    budget = record["budget"] + (extra_budget or 0)
    chunk = {
        "id": f"{file}:{start_line}-{end_line}", "file": file, "start_line": start_line,
        "end_line": end_line, "name": Path(file).name, "chunk_kind": "adhoc", "file_kind": "adhoc",
        "score": None, "reasons": [reason or "manually added by agent"], "is_seed": False,
    }
    chunk["tokens"] = _chunk_tokens(repo_root, chunk)
    if record["used_tokens"] + chunk["tokens"] > budget:
        return {"record": record, "added": False,
                "error": f"would need {record['used_tokens'] + chunk['tokens']} tokens (budget {budget}); "
                         f"pass --budget-extra to allow"}
    record["budget"] = budget
    record["supporting_context"].append(chunk)
    record["used_tokens"] += chunk["tokens"]
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return {"record": record, "added": True, "error": None}

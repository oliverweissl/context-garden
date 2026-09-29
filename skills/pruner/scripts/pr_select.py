"""Budgeted chunk selection + slice persistence for progressive expansion.

Greedy fill in descending score order (not an exact knapsack) so every inclusion
is explainable in `reasons`; tokens are counted without overlap. See references/scoring.md.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path, PurePosixPath

from pr_common import estimate_tokens, is_fixture_path, read_text, sha256_text
from pr_graph import bfs_distances, file_of, merge_external_graph
from pr_index import python_source_roots
from pr_score import (
    Idf,
    TaskText,
    chunk_idf_docs,
    error_identifiers,
    failing_test_names,
    graph_score,
    lexical_match,
    resolve_error_locations,
)

REQUIRED_THRESHOLD = 8.0
SEED_THRESHOLD = 9.0
FALLBACK_SEED_MIN = 4.0  # no strong seed: top lexical chunks >= this seed instead
FALLBACK_SEEDS = 3
MIN_RELATIVE_SCORE = 0.15  # chunks below this fraction of the top score are omitted
TEST_BUDGET_FRACTION = 0.2  # cap for test chunks the task/error doesn't name
TEST_BUDGET_FRACTION_NAMED = 0.1  # ...when it does name specific failing tests
LARGE_CLASS_LINES = 30  # larger classes are represented by a header chunk
HEADER_MAX_LINES = 20
CONFIG_PRIORITY_NAMES = {"cmakelists.txt", "pyproject.toml", "setup.py", "setup.cfg", "makefile", "package.json"}
OMITTED_CAP = 30
SLICES_DIRNAME = "slices"


def _all_chunks(index) -> list[dict]:
    """One chunk per symbol, plus a whole-file chunk for symbol-less files.
    Large classes become `class_header` chunks (see references/scoring.md);
    their methods stay separate chunks, so never select the whole class."""
    chunks = []
    for rel, entry in index.files.items():
        syms = entry["symbols"]
        if not syms:
            chunks.append(
                {
                    "id": f"{rel}:__file__",
                    "file": rel,
                    "start_line": 1,
                    "end_line": max(1, entry["line_count"]),
                    "name": Path(rel).name,
                    "qualname": Path(rel).name,
                    "doc": "",
                    "chunk_kind": "file",
                    "file_kind": entry["kind"],
                    "tokens": entry["size_tokens"],
                }
            )
            continue
        for sym in syms:
            c = {
                "id": sym["id"],
                "file": rel,
                "start_line": sym["start_line"],
                "end_line": sym["end_line"],
                "name": sym["name"],
                "qualname": sym["qualname"],
                "doc": sym.get("doc", ""),
                "chunk_kind": sym["type"],
                "file_kind": entry["kind"],
                "tokens": sym.get("tokens"),
            }
            kids = [index.by_id[k] for k in index.children.get(sym["id"], ()) if k in index.by_id]
            n_lines = sym["end_line"] - sym["start_line"] + 1
            if kids and sym["type"] in ("class", "type") and n_lines > LARGE_CLASS_LINES:
                first = min(kids, key=lambda k: k["start_line"])
                end = max(sym["start_line"], first["start_line"])
                if first["name"] not in ("__init__", sym["name"]):
                    end = max(sym["start_line"], first["start_line"] - 1)
                end = min(end, sym["start_line"] + HEADER_MAX_LINES - 1)  # long docstrings
                c.update(end_line=end, chunk_kind="class_header", tokens=None)
            chunks.append(c)
    return chunks


class _Lines:
    """Per-file line cache + token estimates, read lazily."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self.cache:
            text = read_text(self.repo_root / rel)
            self.cache[rel] = None if text is None else text.splitlines()
        return self.cache[rel]

    def tokens(self, rel: str, line_numbers) -> int:
        lines = self.lines(rel)
        if lines is None:
            return 1
        return estimate_tokens("\n".join(lines[n - 1] for n in sorted(line_numbers) if 0 < n <= len(lines)))


def _chunk_tokens(repo_root: Path, chunk: dict, cache: dict | None = None) -> int:
    lines = _Lines(repo_root)
    if cache is not None:
        lines.cache = cache
    return lines.tokens(chunk["file"], range(chunk["start_line"], chunk["end_line"] + 1))


def _nearest_configs(index, anchors: set[str]) -> dict[str, str]:
    """config file -> the anchor source file it is nearest to: walk up from
    each anchor to the first directory holding a primary project config
    (CMakeLists.txt, pyproject.toml, ...). Test-fixture configs never count."""
    by_dir: dict[str, list[str]] = {}
    for rel, entry in index.files.items():
        p = PurePosixPath(rel)
        if entry["kind"] == "config" and p.name.lower() in CONFIG_PRIORITY_NAMES and not is_fixture_path(rel):
            by_dir.setdefault("" if str(p.parent) == "." else str(p.parent), []).append(rel)
    out: dict[str, str] = {}
    for a in sorted(anchors):
        d = PurePosixPath(a).parent
        while True:
            key = "" if str(d) == "." else str(d)
            if key in by_dir:
                for cfg in by_dir[key]:
                    out.setdefault(cfg, a)
                break
            if key == "":
                break
            d = d.parent
    return out


def _linked_whole_files(index, repo_root: Path, seeds: set[str], by_id: dict) -> dict:
    """Symbol-less source files (declaration-only headers, re-export
    modules) that a seed's file includes or is included by. Scored by
    whether they actually mention a seed's name -- e.g. the header that
    declares the function an 'undeclared identifier' error is about."""
    seed_syms = [index.by_id[s] for s in seeds if s in index.by_id]
    names = {s["name"] for s in seed_syms}
    lines = _Lines(repo_root)
    out = {}
    for sf in {s["file"] for s in seed_syms}:
        for rel in index.imports(sf) | index.importers(sf):
            entry = index.files.get(rel)
            cid = f"{rel}:__file__"
            if not entry or entry["symbols"] or entry["language"] is None or cid not in by_id:
                continue
            text = "\n".join(lines.lines(rel) or [])
            hits = sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", text))
            if hits:
                out[cid] = (5.0, f"linked to a seed's file and mentions {hits}")
            else:
                out.setdefault(cid, (1.0, "included by / includes a seed's file"))
    return out


def score_all(
    index,
    repo_root: Path,
    task: str,
    changed_files: list[str] | None,
    error_text: str | None,
    external_graph: dict | None,
    budget: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Returns (chunks sorted by score desc, notes)."""
    # identifiers named by the error text (exception type, failing test,
    # quoted names, echoed code) count as task text for lexical scoring
    err_idents = " ".join(error_identifiers(error_text)) if error_text else ""
    tt = TaskText(f"{task or ''} {err_idents}".strip())
    named_tests = {n.lower() for n in failing_test_names(error_text)} | {
        w for w in TaskText(task or "").words if w.startswith("test")
    }
    chunks = _all_chunks(index)
    changed_set = set(changed_files or [])
    source_roots = set(python_source_roots(index.files))
    error_seed_ids: set[str] = set()
    notes: list[str] = []
    if error_text:
        hit_ids, synthetic, notes = resolve_error_locations(error_text, index, source_roots)
        error_seed_ids = set(hit_ids)
        chunks.extend(synthetic)

    idf = Idf(chunk_idf_docs(chunks))
    lexical = {c["id"]: lexical_match(c, tt, idf) for c in chunks}
    by_id = {c["id"]: c for c in chunks}

    # graph seeds: strong lexical matches (a rare exact name, a qualified
    # name) and error-stack hits -- not every single-word overlap
    seeds = {cid for cid, (s, _) in lexical.items() if s >= SEED_THRESHOLD}
    seeds |= error_seed_ids
    if not seeds:
        ranked = sorted(
            (
                (s, cid)
                for cid, (s, _) in lexical.items()
                if s >= FALLBACK_SEED_MIN and by_id[cid]["file_kind"] not in ("test", "config", "doc")
            ),
            reverse=True,
        )
        if ranked:
            floor = 0.6 * ranked[0][0]
            seeds = {cid for s, cid in ranked[:FALLBACK_SEEDS] if s >= floor}
    if not seeds:
        seeds = {c["id"] for c in chunks if c["file"] in changed_set}

    dist: dict[str, int] = {}
    if seeds:
        extra = merge_external_graph(external_graph) if external_graph else None
        dist = bfs_distances(index, seeds, extra)
        # what a seed calls/uses is slightly more useful than who calls it
        seed_callees = set().union(*(index.callees(sd) for sd in seeds if sd in index.by_id))
    else:
        seed_callees = set()

    seed_files = {file_of(s) for s in seeds}
    fallback = _linked_whole_files(index, repo_root, seeds, by_id)
    test_target_cache: dict[str, set[str]] = {}
    scored = []
    for c in chunks:
        lex_score, lex_reasons = lexical[c["id"]]
        reasons = list(lex_reasons)
        g_score, g_reason = graph_score(dist.get(c["id"]))
        if g_reason:
            reasons.append(g_reason)
            if c["id"] in seed_callees and dist.get(c["id"]) == 1:
                g_score += 0.5
                reasons.append("called/used by a seed")
        elif c["id"] in fallback:
            g_score, g_reason = fallback[c["id"]]
            reasons.append(g_reason)

        test_boost = 0.0
        named_test = False
        if c["file_kind"] == "test":
            if c["file"] not in test_target_cache:
                test_target_cache[c["file"]] = index.test_targets(c["file"])
            if test_target_cache[c["file"]] & seed_files:
                test_boost = 8.0
                reasons.append("tests a file the task/error/diff implicates")
            named_test = c["name"].lower() in named_tests or c["id"] in error_seed_ids

        recency_boost = 0.0
        if c["file"] in changed_set:
            recency_boost = 6.0
            reasons.append("file was recently changed")

        error_boost = 0.0
        if c["id"] in error_seed_ids:
            error_boost = 15.0
            reasons.append("encloses a location named in the provided error/traceback")

        total = lex_score + g_score + test_boost + recency_boost + error_boost
        if c["file_kind"] == "config" and is_fixture_path(c["file"]) and not error_boost:
            total = 0.0  # a test fixture's pyproject/CMakeLists is never the project's config
        scored.append(
            {
                **c,
                "score": round(total, 2),
                "reasons": reasons,
                "is_seed": c["id"] in seeds,
                "named_test": named_test,
            }
        )

    # primary config: the nearest one(s) above the selected source files
    anchors = {
        c["file"]
        for c in scored
        if c["file_kind"] not in ("test", "config", "doc")
        and (c["is_seed"] or c["score"] >= REQUIRED_THRESHOLD)
    }
    if not anchors:
        top = sorted(
            (c for c in scored if c["score"] > 0 and c["file_kind"] not in ("test", "config", "doc")),
            key=lambda c: -c["score"],
        )[:3]
        anchors = {c["file"] for c in top}
    nearest = _nearest_configs(index, anchors)
    for c in scored:
        if c["file_kind"] == "config" and c["file"] in nearest:
            c["score"] = round(max(c["score"], 0.0) + 0.5, 2)
            c["reasons"].append(f"nearest project configuration to {nearest[c['file']]}")

    scored.sort(key=lambda c: -c["score"])
    return scored, notes


def select(
    index,
    repo_root: Path,
    task: str,
    budget: int,
    changed_files: list[str] | None = None,
    error_text: str | None = None,
    external_graph: dict | None = None,
) -> dict:
    scored, notes = score_all(
        index, repo_root, task, changed_files, error_text, external_graph, budget
    )
    lines = _Lines(repo_root)
    any_named = any(c["named_test"] for c in scored)
    test_cap = int(budget * (TEST_BUDGET_FRACTION_NAMED if any_named else TEST_BUDGET_FRACTION))

    top = max((c["score"] for c in scored if c["file_kind"] != "config"), default=0.0)
    floor = MIN_RELATIVE_SCORE * top
    used = test_used = 0
    covered: dict[str, set[int]] = {}
    required, supporting, tests, config, omitted = [], [], [], [], []
    for c in scored:
        if c["score"] <= 0 or (c["score"] < floor and c["file_kind"] != "config"):
            omitted.append(c)
            continue
        span = set(range(c["start_line"], c["end_line"] + 1))
        already = covered.get(c["file"], set())
        new = span - already
        if not new:
            continue  # nested in something already selected
        if c.get("tokens") is None:
            c["tokens"] = lines.tokens(c["file"], span)
        cost = c["tokens"] if new == span else lines.tokens(c["file"], new)
        capped_test = c["file_kind"] == "test" and not c["named_test"]
        if used + cost > budget or (capped_test and test_used + cost > test_cap):
            omitted.append(c)
            continue
        if new != span:
            c["reasons"].append(f"overlaps already-selected lines; counted {cost} new tokens")
        c["tokens"] = cost
        used += cost
        if capped_test:
            test_used += cost
        covered.setdefault(c["file"], set()).update(span)
        if c["file_kind"] == "test":
            tests.append(c)
        elif c["file_kind"] == "config":
            config.append(c)
        elif c["score"] >= REQUIRED_THRESHOLD:
            required.append(c)
        else:
            supporting.append(c)

    omitted.sort(key=lambda c: -c["score"])
    omitted = omitted[:OMITTED_CAP]
    for c in omitted:
        if c.get("tokens") is None:
            c["tokens"] = lines.tokens(c["file"], range(c["start_line"], c["end_line"] + 1))
    for c in required + supporting + tests + config + omitted:
        c.pop("named_test", None)
    # "high" needs a non-test source chunk in required_context; a slice
    # that is only tests/weak matches is at best "medium"
    confidence = "high" if required else ("medium" if (supporting or tests) else "low")

    result = {
        "task": task,
        "budget": budget,
        "used_tokens": used,
        "required_context": required,
        "supporting_context": supporting,
        "relevant_tests": tests,
        "relevant_config": config,
        "omitted_candidates": omitted,
        "confidence": confidence,
    }
    if notes:
        result["notes"] = notes
    if confidence != "high":
        result["hint"] = (
            "no source chunk scored as required -- this slice may not explain the task; "
            "verify with grep/targeted reads before relying on it"
        )
    return result


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


_INCLUDED_KEYS = ("required_context", "supporting_context", "relevant_tests", "relevant_config")


def _file_hash(repo_root: Path, rel: str) -> str | None:
    text = read_text(repo_root / rel)
    return None if text is None else sha256_text(text)


def _record_file_hashes(record: dict) -> None:
    """Remember the content hash of every file in the slice (first time it
    is seen), so `show` can warn when line ranges may have gone stale."""
    repo_root = Path(record["repo_root"])
    hashes = record.setdefault("file_hashes", {})
    for key in _INCLUDED_KEYS:
        for c in record.get(key, []):
            if c["file"] not in hashes:
                hashes[c["file"]] = _file_hash(repo_root, c["file"])


def changed_since_slice(record: dict) -> list[str]:
    """Files in the slice whose content changed since their hash was stored."""
    repo_root = Path(record["repo_root"])
    return sorted(
        f
        for f, h in record.get("file_hashes", {}).items()
        if _file_hash(repo_root, f) != h
    )


def save_slice(store_dir: Path, slice_id: str, repo_root: Path, result: dict) -> Path:
    record = {
        "slice_id": slice_id,
        "repo_root": str(repo_root),
        "created_at": time.time(),
        **result,
    }
    _record_file_hashes(record)
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def load_slice(store_dir: Path, slice_id: str) -> dict:
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no slice {slice_id}")
    return json.loads(path.read_text())


def expand_slice(
    store_dir: Path, slice_id: str, add_chunk_ids: list[str], extra_budget: int | None = None
) -> dict:
    """Promote omitted candidates (by chunk id, or file:start-end) into the
    slice's supporting_context without rescoring; still budget-enforced."""
    record = load_slice(store_dir, slice_id)
    budget = record["budget"] + (extra_budget or 0)
    omitted_by_id = {c["id"]: c for c in record["omitted_candidates"]}
    for c in record["omitted_candidates"]:
        omitted_by_id.setdefault(f"{c['file']}:{c['start_line']}-{c['end_line']}", c)
    promoted, still_missing, over_budget = [], [], []

    for cid in add_chunk_ids:
        chunk = omitted_by_id.get(cid)
        if chunk is not None and chunk["id"] in promoted:
            continue
        if chunk is None:
            still_missing.append(cid)
            continue
        if record["used_tokens"] + chunk["tokens"] > budget:
            over_budget.append(cid)
            continue
        chunk["reasons"].append("manually expanded: agent identified this as a missing dependency")
        record["supporting_context"].append(chunk)
        record["used_tokens"] += chunk["tokens"]
        record["omitted_candidates"] = [
            c for c in record["omitted_candidates"] if c["id"] != chunk["id"]
        ]
        promoted.append(chunk["id"])

    record["budget"] = budget
    _record_file_hashes(record)
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return {
        "record": record,
        "promoted": promoted,
        "still_missing": still_missing,
        "over_budget": over_budget,
    }


def add_ad_hoc_chunk(
    store_dir: Path,
    slice_id: str,
    repo_root: Path,
    file: str,
    start_line: int,
    end_line: int,
    reason: str,
    extra_budget: int | None = None,
) -> dict:
    """Inject a file:line range the scorer never surfaced (e.g. a doc file).
    Budget-enforced: callers raise the ceiling via extra_budget, never silently."""
    record = load_slice(store_dir, slice_id)
    budget = record["budget"] + (extra_budget or 0)
    chunk = {
        "id": f"{file}:{start_line}-{end_line}",
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "name": Path(file).name,
        "chunk_kind": "adhoc",
        "file_kind": "adhoc",
        "score": None,
        "reasons": [reason or "manually added by agent"],
        "is_seed": False,
    }
    chunk["tokens"] = _chunk_tokens(repo_root, chunk)
    if record["used_tokens"] + chunk["tokens"] > budget:
        return {
            "record": record,
            "added": False,
            "error": (
                f"would need {record['used_tokens'] + chunk['tokens']} tokens (budget {budget}); "
                f"pass --budget-extra to allow"
            ),
        }
    record["budget"] = budget
    record["supporting_context"].append(chunk)
    record["used_tokens"] += chunk["tokens"]
    _record_file_hashes(record)
    path = _slice_dir(store_dir) / f"{slice_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return {"record": record, "added": True, "error": None}

#!/usr/bin/env python3
"""seedbank: learn which repository knowledge is worth persisting.

See ../SKILL.md for the agent-facing workflow and ../references/ for the
scoring/schema details. Stdlib-only, no network access, no LLM calls --
the agent supplies the semantic "representation" text when it logs a
mistake/fact or promotes a candidate; this tool does the deterministic
bookkeeping (frequency, cost, hashing, scoring, tiering, invalidation,
compilation) around that.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sb_compile
from sb_scoring import candidate_persistent_cost, compute_value, stability
from sb_store import (
    FAILURE_COST_MISTAKE,
    FAILURE_COST_RUN,
    SEARCH_DEFAULT_COST,
    Store,
    estimate_tokens,
    find_store_root,
    now,
    sha256_file,
)

KINDS = ("read", "search", "run", "mistake", "fact")


# ---------------------------------------------------------------- keystats update

def _blank_stats(kind: str, scope: str) -> dict:
    return {
        "kind": kind,
        "scope": scope,
        "representation": None,
        "sources": [],
        "source_hashes": {},
        "hash_changes": 0,
        "access_count": 0,
        "total_retrieval_cost": 0,
        "total_failure_cost": 0,
        "first_seen": now(),
        "last_access": now(),
    }


def update_keystats(store: Store, key: str, kind: str, scope: str, sources: list[str],
                     retrieval_cost: int, failure_cost: int, representation: str | None) -> dict:
    stats_all = store.load_keystats()
    stats = stats_all.get(key) or _blank_stats(kind, scope)
    stats["access_count"] += 1
    stats["total_retrieval_cost"] += retrieval_cost
    stats["total_failure_cost"] += failure_cost
    stats["last_access"] = now()
    if scope:
        stats["scope"] = scope
    if representation:
        stats["representation"] = representation
    for s in sources:
        if s not in stats["sources"]:
            stats["sources"].append(s)
        new_hash = sha256_file(s)
        old_hash = stats["source_hashes"].get(s)
        if old_hash is not None and new_hash != old_hash:
            stats["hash_changes"] += 1
        if new_hash is not None:
            stats["source_hashes"][s] = new_hash
    stats_all[key] = stats
    store.save_keystats(stats_all)
    return stats


# ---------------------------------------------------------------- observe

def cmd_observe_read(args, store: Store) -> int:
    path = args.path
    text = Path(path).read_text(errors="replace") if Path(path).exists() else ""
    cost = estimate_tokens(text)
    key = args.key or f"read:{path}"
    obs = {
        "id": None, "ts": now(), "kind": "read", "key": key,
        "sources": [path], "task": args.task, "cost": cost, "failure_cost": 0,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, "read", args.scope, [path], cost, 0, None)
    print(f"observed read: {key} (access_count={stats['access_count']}, cost~{cost} tok)")
    return 0


def cmd_observe_search(args, store: Store) -> int:
    cost = args.cost if args.cost is not None else SEARCH_DEFAULT_COST
    key = args.key or f"search:{args.pattern}"
    obs = {
        "id": None, "ts": now(), "kind": "search", "key": key,
        "sources": [], "task": args.task, "cost": cost, "failure_cost": 0,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, "search", args.scope, [], cost, 0, None)
    print(f"observed search: {key} (access_count={stats['access_count']}, cost~{cost} tok)")
    return 0


def cmd_observe_run(args, store: Store) -> int:
    cmd_list = args.command
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]
    if not cmd_list:
        print("error: no command given (usage: seedbank observe run -- <command...>)", file=sys.stderr)
        return 2
    command_str = " ".join(cmd_list)
    proc = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout.decode(errors="replace")
    cost = estimate_tokens(text)
    failure_cost = 0 if proc.returncode == 0 else FAILURE_COST_RUN
    key = args.key or f"run:{command_str}"
    obs = {
        "id": None, "ts": now(), "kind": "run", "key": key, "sources": [],
        "task": args.task, "cost": cost, "failure_cost": failure_cost,
        "exit_code": proc.returncode,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, "run", args.scope, [], cost, failure_cost, None)
    status = "ok" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
    print(f"observed run: {key} [{status}] (access_count={stats['access_count']})")
    if text:
        sys.stdout.write(text if len(text) < 2000 else text[:2000] + "\n... (truncated; not stored raw by seedbank -- use compost for that)\n")
    return proc.returncode


def cmd_observe_declared(args, store: Store, kind: str) -> int:
    representation = args.representation.strip()
    key = args.key or f"{kind}:{hashlib.sha1(representation.encode()).hexdigest()[:12]}"
    default_failure = FAILURE_COST_MISTAKE if kind == "mistake" else 0
    failure_cost = args.failure_cost if args.failure_cost is not None else default_failure
    sources = args.source or []
    obs = {
        "id": None, "ts": now(), "kind": kind, "key": key, "sources": sources,
        "task": None, "cost": 0, "failure_cost": failure_cost, "representation": representation,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, kind, args.scope, sources, 0, failure_cost, representation)
    print(f"observed {kind}: {key} (access_count={stats['access_count']})")
    print(f'representation: "{representation}"')
    return 0


# ---------------------------------------------------------------- candidates / promote / demote

def _active_fact_keys(facts: dict) -> set[str]:
    return {f["key"] for f in facts["facts"].values() if not f.get("stale")}


def build_candidates(store: Store, threshold: float, show_all: bool) -> list[dict]:
    keystats = store.load_keystats()
    facts = store.load_facts()
    active_keys = _active_fact_keys(facts)
    out = []
    for key, stats in keystats.items():
        if key in active_keys:
            continue
        pcost = candidate_persistent_cost(stats, estimate_tokens)
        value = compute_value(stats, pcost)
        if show_all or value >= threshold:
            out.append({
                "key": key,
                "kind": stats["kind"],
                "scope": stats.get("scope", "general"),
                "access_count": stats["access_count"],
                "total_retrieval_cost": stats["total_retrieval_cost"],
                "total_failure_cost": stats["total_failure_cost"],
                "stability": stability(stats.get("hash_changes", 0)),
                "representation": stats.get("representation"),
                "value": round(value, 3),
            })
    out.sort(key=lambda c: -c["value"])
    return out


def cmd_candidates(args, store: Store) -> int:
    config = store.load_config()
    threshold = args.threshold if args.threshold is not None else config["promote_threshold"]
    candidates = build_candidates(store, threshold, args.all)
    if args.json:
        print(json.dumps(candidates, indent=2))
        return 0
    if not candidates:
        print(f"no candidates above threshold {threshold} (use --all to see everything)")
        return 0
    print(f"{'value':>7}  {'n':>3}  {'kind':<8} {'scope':<12} key")
    for c in candidates:
        print(f"{c['value']:>7.2f}  {c['access_count']:>3}  {c['kind']:<8} {c['scope']:<12} {c['key']}")
        if c["representation"]:
            print(f"         representation: {c['representation']}")
        else:
            print("         representation: (none yet -- supply one with `seedbank promote --representation`)")
    return 0


def cmd_promote(args, store: Store) -> int:
    keystats = store.load_keystats()
    stats = keystats.get(args.key)
    if not stats:
        print(f"error: no observations for key '{args.key}' (see `seedbank candidates`)", file=sys.stderr)
        return 1
    representation = args.representation or stats.get("representation")
    if not representation:
        print("error: no representation available -- pass --representation \"...\"", file=sys.stderr)
        return 1

    facts = store.load_facts()
    pcost = estimate_tokens(representation)
    tier = args.tier

    if tier == "hot":
        config = store.load_config()
        budget = args.budget if args.budget is not None else config["hot_budget"]
        current_hot = [f for f in facts["facts"].values() if f["tier"] == "hot" and not f.get("stale")]
        current_total = sum(f["persistent_token_cost"] for f in current_hot)
        if current_total + pcost > budget:
            evicted = _evict_to_budget(facts, budget - pcost, protect_keys={args.key})
            if evicted is None:
                print(
                    f"error: promoting to hot would need {current_total + pcost} tokens "
                    f"(budget {budget}) and there is nothing evictable (remaining hot facts "
                    f"are --critical). Raise --budget, demote something manually, or use --tier warm.",
                    file=sys.stderr,
                )
                return 1
            for fid in evicted:
                print(f"evicted (demoted to warm) to stay within hot budget: {fid}")

    fid = Store.gen_fact_id(facts)
    scope = args.scope or stats.get("scope", "general")
    fact = {
        "id": fid,
        "key": args.key,
        "representation": representation,
        "scope": scope,
        "sources": list(stats.get("sources", [])),
        "source_hashes": dict(stats.get("source_hashes", {})),
        "access_count_at_promotion": stats["access_count"],
        "retrieval_cost_at_promotion": stats["total_retrieval_cost"],
        "failure_cost_at_promotion": stats["total_failure_cost"],
        "confidence": 0.8 if stats["kind"] in ("mistake", "fact") else 0.6,
        "stability": stability(stats.get("hash_changes", 0)),
        "persistent_token_cost": pcost,
        "tier": tier,
        "protected": bool(args.critical),
        "stale": False,
        "stale_reason": None,
        "promoted_at": now(),
        "last_verified": now(),
    }
    facts["facts"][fid] = fact
    store.save_facts(facts)
    print(f"promoted {fid} [{tier}] scope={scope} cost={pcost}tok: \"{representation}\"")
    return 0


def _evict_to_budget(facts: dict, target_max: int, protect_keys: set[str]) -> list[str] | None:
    """Demote lowest-value non-protected hot facts (to warm) until hot total <= target_max.
    Returns list of demoted fact ids, or None if it can't fit (all remaining are protected)."""
    keystats_placeholder = {}  # scoring uses snapshot stats stored on the fact itself
    hot = [f for f in facts["facts"].values() if f["tier"] == "hot" and not f.get("stale")]
    total = sum(f["persistent_token_cost"] for f in hot)
    if total <= target_max:
        return []

    def fact_value(f):
        stats = {
            "access_count": f["access_count_at_promotion"],
            "total_retrieval_cost": f["retrieval_cost_at_promotion"],
            "total_failure_cost": f["failure_cost_at_promotion"],
            "hash_changes": 0 if f["stability"] >= 1.0 else round(1.0 / f["stability"] - 1.0),
        }
        return compute_value(stats, f["persistent_token_cost"])

    evictable = sorted(
        (f for f in hot if not f["protected"] and f["key"] not in protect_keys),
        key=fact_value,
    )
    demoted = []
    for f in evictable:
        if total <= target_max:
            break
        f["tier"] = "warm"
        demoted.append(f["id"])
        total -= f["persistent_token_cost"]
    if total > target_max:
        # roll back: nothing evictable left but still over budget
        for fid in demoted:
            facts["facts"][fid]["tier"] = "hot"
        return None
    return demoted


def cmd_demote(args, store: Store) -> int:
    facts = store.load_facts()
    fact = facts["facts"].get(args.fact_id)
    if not fact:
        print(f"error: no fact {args.fact_id}", file=sys.stderr)
        return 1
    if args.tier == "cold":
        del facts["facts"][args.fact_id]
        store.save_facts(facts)
        print(f"removed {args.fact_id} (demoted to cold)")
        return 0
    fact["tier"] = args.tier
    store.save_facts(facts)
    print(f"demoted {args.fact_id} to {args.tier}")
    return 0


# ---------------------------------------------------------------- invalidate

def cmd_invalidate(args, store: Store) -> int:
    facts = store.load_facts()
    if args.confirm:
        fact = facts["facts"].get(args.confirm)
        if not fact:
            print(f"error: no fact {args.confirm}", file=sys.stderr)
            return 1
        for s in fact["sources"]:
            h = sha256_file(s)
            if h is not None:
                fact["source_hashes"][s] = h
        fact["stale"] = False
        fact["stale_reason"] = None
        fact["last_verified"] = now()
        fact["confidence"] = min(1.0, fact.get("confidence", 0.8) + 0.05)
        store.save_facts(facts)
        print(f"revalidated {args.confirm}: stale cleared, hashes refreshed, confidence={fact['confidence']:.2f}")
        return 0

    changed = 0
    for fact in facts["facts"].values():
        if not fact["sources"]:
            continue  # policy-style fact with no backing file: nothing to check
        stale_reason = None
        for s, old_hash in fact["source_hashes"].items():
            new_hash = sha256_file(s)
            if new_hash is None:
                stale_reason = f"source missing: {s}"
                break
            if new_hash != old_hash:
                stale_reason = f"source changed: {s}"
                break
        was_stale = fact.get("stale", False)
        fact["stale"] = stale_reason is not None
        fact["stale_reason"] = stale_reason
        if fact["stale"] and not was_stale:
            changed += 1
    store.save_facts(facts)
    stale_now = [f["id"] for f in facts["facts"].values() if f.get("stale")]
    print(f"invalidation scan: {changed} newly stale, {len(stale_now)} total stale")
    for fid in stale_now:
        f = facts["facts"][fid]
        print(f"  STALE {fid}: {f['stale_reason']}  -- \"{f['representation'][:80]}\"")
    return 0


# ---------------------------------------------------------------- gc

def cmd_gc(args, store: Store) -> int:
    facts = store.load_facts()
    config = store.load_config()
    budget = config["hot_budget"]
    evicted = _evict_to_budget(facts, budget, protect_keys=set())
    if evicted:
        for fid in evicted:
            print(f"evicted (over hot budget): {fid} -> warm")
    elif evicted is None:
        print("warning: hot tier over budget and nothing evictable (all --critical)", file=sys.stderr)

    removed_stale = 0
    if args.purge_stale:
        stale_ids = [fid for fid, f in facts["facts"].items() if f.get("stale")]
        for fid in stale_ids:
            del facts["facts"][fid]
        removed_stale = len(stale_ids)

    store.save_facts(facts)
    removed_obs = store.prune_observations(keep_per_key=args.keep_per_key)
    print(f"gc: {removed_obs} raw observation(s) pruned (aggregates preserved), {removed_stale} stale fact(s) purged")
    return 0


# ---------------------------------------------------------------- compile

def cmd_compile(args, store: Store, repo_root: Path) -> int:
    cmd_invalidate(argparse.Namespace(confirm=None), store)
    facts = store.load_facts()
    targets = args.targets.split(",") if args.targets else ["AGENTS.md"]
    report = sb_compile.compile_outputs(facts, repo_root, targets)
    print(f"compiled {report['hot_fact_count']} hot fact(s) [{report['hot_tokens']} tok] "
          f"+ {report['warm_fact_count']} warm fact(s)")
    for path in report["written"]:
        print(f"  wrote {path}")
    if report["stale_excluded"]:
        print(f"excluded {len(report['stale_excluded'])} stale fact(s) (not exposed): {', '.join(report['stale_excluded'])}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- status / stats

def cmd_status(args, store: Store) -> int:
    keystats = store.load_keystats()
    facts = store.load_facts()["facts"]
    config = store.load_config()
    hot = [f for f in facts.values() if f["tier"] == "hot" and not f.get("stale")]
    warm = [f for f in facts.values() if f["tier"] == "warm" and not f.get("stale")]
    stale = [f for f in facts.values() if f.get("stale")]
    hot_tokens = sum(f["persistent_token_cost"] for f in hot)
    candidates = build_candidates(store, config["promote_threshold"], show_all=False)

    print(f"tracked keys:        {len(keystats)}")
    print(f"active facts:        {len(facts)}  (hot={len(hot)}, warm={len(warm)}, stale={len(stale)})")
    print(f"hot budget usage:    {hot_tokens} / {config['hot_budget']} tokens")
    print(f"candidates >= threshold ({config['promote_threshold']}): {len(candidates)}")
    if stale:
        print("stale facts (need `seedbank invalidate --confirm <id>` or `demote`):")
        for f in stale:
            print(f"  {f['id']}: {f['stale_reason']}")
    return 0


def cmd_stats(args, store: Store) -> int:
    facts = store.load_facts()["facts"]
    total_avoided = 0
    rows = []
    for f in facts.values():
        if f.get("stale"):
            continue
        avoided = max(0, f["retrieval_cost_at_promotion"] - f["persistent_token_cost"])
        total_avoided += avoided
        rows.append((f["id"], avoided, f["tier"], f["representation"]))
    rows.sort(key=lambda r: -r[1])
    if args.json:
        print(json.dumps({"total_tokens_avoided": total_avoided, "facts": [
            {"id": r[0], "tokens_avoided": r[1], "tier": r[2], "representation": r[3]} for r in rows
        ]}, indent=2))
        return 0
    print(f"estimated tokens avoided so far: {total_avoided}")
    print("(one-time savings already realized by promoting: pre-promotion rediscovery cost")
    print(" minus the persistent cost of keeping the compact fact around; does not yet count")
    print(" future avoided rediscoveries -- see validate.md for the full definition)")
    for fid, avoided, tier, rep in rows:
        print(f"  {fid} [{tier}] ~{avoided} tok avoided: {rep[:70]}")
    return 0


# ---------------------------------------------------------------- import

def cmd_import(args, store: Store) -> int:
    text = Path(args.file).read_text(errors="replace")
    scope = "general"
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            scope = stripped.lstrip("#").strip().lower().replace(" ", "-") or "general"
            continue
        if stripped.startswith(("- ", "* ")):
            rep = stripped[2:].strip()
            if not rep:
                continue
            key = f"imported:{hashlib.sha1(rep.encode()).hexdigest()[:12]}"
            obs = {"id": None, "ts": now(), "kind": "imported", "key": key, "sources": [args.file],
                   "task": None, "cost": 0, "failure_cost": 0, "representation": rep}
            store.append_observation(obs)
            update_keystats(store, key, "imported", scope, [], 0, 0, rep)
            count += 1
    print(f"imported {count} candidate bullet(s) from {args.file} (review with `seedbank candidates`, then promote)")
    return 0


# ---------------------------------------------------------------- CLI wiring

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seedbank")
    p.add_argument("--store", default=None, help="override store directory (default: ./.seedbank)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_obs = sub.add_parser("observe", help="record a repository access or a declared fact/mistake")
    obs_sub = p_obs.add_subparsers(dest="obs_kind", required=True)

    p_read = obs_sub.add_parser("read")
    p_read.add_argument("path")
    p_read.add_argument("--task", default=None)
    p_read.add_argument("--scope", default="general")
    p_read.add_argument("--key", default=None)

    p_search = obs_sub.add_parser("search")
    p_search.add_argument("pattern")
    p_search.add_argument("--task", default=None)
    p_search.add_argument("--scope", default="general")
    p_search.add_argument("--key", default=None)
    p_search.add_argument("--cost", type=int, default=None)

    p_run = obs_sub.add_parser("run")
    p_run.add_argument("--task", default=None)
    p_run.add_argument("--scope", default="general")
    p_run.add_argument("--key", default=None)
    p_run.add_argument("command", nargs=argparse.REMAINDER)

    p_mistake = obs_sub.add_parser("mistake")
    p_mistake.add_argument("representation")
    p_mistake.add_argument("--scope", default="invariants")
    p_mistake.add_argument("--key", default=None)
    p_mistake.add_argument("--source", action="append", default=None)
    p_mistake.add_argument("--failure-cost", type=int, default=None)

    p_fact = obs_sub.add_parser("fact")
    p_fact.add_argument("representation")
    p_fact.add_argument("--scope", default="general")
    p_fact.add_argument("--key", default=None)
    p_fact.add_argument("--source", action="append", default=None)
    p_fact.add_argument("--failure-cost", type=int, default=None)

    p_cand = sub.add_parser("candidates", help="list promotion candidates ranked by value")
    p_cand.add_argument("--threshold", type=float, default=None)
    p_cand.add_argument("--all", action="store_true")
    p_cand.add_argument("--json", action="store_true")

    p_promote = sub.add_parser("promote", help="promote a candidate key to a persistent fact")
    p_promote.add_argument("key")
    p_promote.add_argument("--representation", default=None)
    p_promote.add_argument("--tier", choices=["hot", "warm"], default="warm")
    p_promote.add_argument("--scope", default=None)
    p_promote.add_argument("--critical", action="store_true", help="protect from value-based eviction")
    p_promote.add_argument("--budget", type=int, default=None)

    p_demote = sub.add_parser("demote", help="lower a fact's tier, or remove it (--tier cold)")
    p_demote.add_argument("fact_id")
    p_demote.add_argument("--tier", choices=["warm", "cold"], required=True)

    p_inval = sub.add_parser("invalidate", help="scan facts for changed sources, or confirm one is still valid")
    p_inval.add_argument("--confirm", default=None, metavar="FACT_ID")

    p_gc = sub.add_parser("gc", help="enforce hot budget, purge stale facts, prune observation log")
    p_gc.add_argument("--purge-stale", action="store_true")
    p_gc.add_argument("--keep-per-key", type=int, default=5)

    p_compile = sub.add_parser("compile", help="write AGENTS.md (+ warm/*.md) from active facts")
    p_compile.add_argument("--targets", default=None, help="comma-separated output filenames (default: AGENTS.md)")

    sub.add_parser("status", help="quick health summary")

    p_stats = sub.add_parser("stats", help="report estimated tokens avoided")
    p_stats.add_argument("--json", action="store_true")

    p_import = sub.add_parser("import", help="seed candidates from an existing AGENTS.md/CLAUDE.md-style file")
    p_import.add_argument("file")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(find_store_root(args.store))

    if args.cmd == "observe":
        handlers = {
            "read": cmd_observe_read,
            "search": cmd_observe_search,
            "run": cmd_observe_run,
            "mistake": lambda a, s: cmd_observe_declared(a, s, "mistake"),
            "fact": lambda a, s: cmd_observe_declared(a, s, "fact"),
        }
        try:
            return handlers[args.obs_kind](args, store)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    top_handlers = {
        "candidates": cmd_candidates,
        "promote": cmd_promote,
        "demote": cmd_demote,
        "invalidate": cmd_invalidate,
        "gc": cmd_gc,
        "status": cmd_status,
        "stats": cmd_stats,
        "import": cmd_import,
    }
    if args.cmd == "compile":
        return cmd_compile(args, store, Path.cwd())
    try:
        return top_handlers[args.cmd](args, store)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

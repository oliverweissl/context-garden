#!/usr/bin/env python3
"""seedbank: learn which repository knowledge is worth persisting.

The agent supplies the fact text; this tool only does the bookkeeping
around it. See ../SKILL.md and ../references/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sb_compile
from sb_conflicts import conflicts
from sb_scoring import (
    candidate_persistent_cost,
    compute_value,
    decay,
    distinct_sessions,
    stability,
    value_kwargs,
)
from sb_store import (
    FAILURE_COST_MISTAKE,
    FAILURE_COST_RUN,
    SEARCH_DEFAULT_COST,
    Store,
    estimate_tokens,
    find_repo_root,
    find_store_root,
    now,
    rel_to_root,
    resolve_session,
    safe_scope,
    sha256_file,
)

KINDS = ("read", "search", "run", "mistake", "fact")
MAX_TRACKED_SESSIONS = 32  # session ids kept per key; session_count keeps counting


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
        "session_count": 0,
        "sessions": [],
        "total_retrieval_cost": 0,
        "total_failure_cost": 0,
        "first_seen": now(),
        "last_access": now(),
    }


def update_keystats(
    store: Store,
    key: str,
    kind: str,
    scope: str,
    sources: list[str],
    retrieval_cost: int,
    failure_cost: int,
    representation: str | None,
) -> dict:
    stats_all = store.load_keystats()
    stats = stats_all.get(key) or _blank_stats(kind, safe_scope(scope))
    if "session_count" not in stats:  # migrate a pre-session-tracking key
        stats["session_count"] = distinct_sessions(stats)
        stats["sessions"] = []
        if stats.get("kind") == "fact" and not stats.get("total_failure_cost"):
            stats["total_failure_cost"] = store.load_config()["fact_failure_cost"]
    session = getattr(store, "session", None) or resolve_session()
    if session not in stats["sessions"]:
        stats["session_count"] += 1
        stats["sessions"] = (stats["sessions"] + [session])[-MAX_TRACKED_SESSIONS:]
    stats["access_count"] += 1
    stats["total_retrieval_cost"] += retrieval_cost
    stats["total_failure_cost"] += failure_cost
    stats["last_access"] = now()
    if scope:
        stats["scope"] = safe_scope(scope)
    if representation:
        stats["representation"] = representation
    for s in sources:
        if s not in stats["sources"]:
            stats["sources"].append(s)
        new_hash = sha256_file(store.source_path(s))
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
    path = rel_to_root(args.path, store.repo_root)
    full = store.source_path(path)
    if not full.is_file():
        print(f"error: no such file: {args.path} (nothing recorded)", file=sys.stderr)
        return 1
    text = full.read_text(errors="replace")
    cost = estimate_tokens(text)
    key = args.key or f"read:{path}"
    obs = {
        "id": None,
        "ts": now(),
        "kind": "read",
        "key": key,
        "sources": [path],
        "task": args.task,
        "session": store.session,
        "cost": cost,
        "failure_cost": 0,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, "read", args.scope, [path], cost, 0, None)
    print(
        f"observed read: {key} (access_count={stats['access_count']}, "
        f"sessions={stats['session_count']}, cost~{cost} tok)"
    )
    return 0


def cmd_observe_search(args, store: Store) -> int:
    cost = args.cost if args.cost is not None else SEARCH_DEFAULT_COST
    key = args.key or f"search:{args.pattern}"
    obs = {
        "id": None,
        "ts": now(),
        "kind": "search",
        "key": key,
        "sources": [],
        "task": args.task,
        "session": store.session,
        "cost": cost,
        "failure_cost": 0,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, "search", args.scope, [], cost, 0, None)
    print(
        f"observed search: {key} (access_count={stats['access_count']}, "
        f"sessions={stats['session_count']}, cost~{cost} tok)"
    )
    return 0


def cmd_observe_run(args, store: Store) -> int:
    cmd_list = args.command
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]
    if not cmd_list:
        print(
            "error: no command given (usage: seedbank observe run -- <command...>)", file=sys.stderr
        )
        return 2
    command_str = " ".join(cmd_list)
    timed_out = False
    try:
        proc = subprocess.run(
            cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.timeout
        )
        raw, returncode = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as e:  # subprocess.run kills the child on timeout
        timed_out = True
        raw, returncode = (e.stdout or b""), 124
    text = raw.decode(errors="replace")
    cost = estimate_tokens(text)
    failure_cost = 0 if returncode == 0 else FAILURE_COST_RUN
    key = args.key or f"run:{command_str}"
    obs = {
        "id": None,
        "ts": now(),
        "kind": "run",
        "key": key,
        "sources": [],
        "task": args.task,
        "session": store.session,
        "cost": cost,
        "failure_cost": failure_cost,
        "exit_code": returncode,
        "timed_out": timed_out,
    }
    with store.lock():  # main() leaves `observe run` unlocked so the command itself runs lock-free
        store.append_observation(obs)
        stats = update_keystats(store, key, "run", args.scope, [], cost, failure_cost, None)
    if timed_out:
        status = f"FAILED (timed out after {args.timeout}s, killed)"
    else:
        status = "ok" if returncode == 0 else f"FAILED (exit {returncode})"
    print(f"observed run: {key} [{status}] (access_count={stats['access_count']})")
    if text:
        sys.stdout.write(
            text
            if len(text) < 2000
            else text[:2000]
            + "\n... (truncated; not stored raw by seedbank -- use compost for that)\n"
        )
    return returncode


def normalize_rep(text: str) -> str:
    """Hashing form of a fact/mistake: lowercase, collapsed whitespace, no
    trailing punctuation, so trivial variants share one key. Display text
    (the stored representation) is kept as the agent wrote it."""
    return " ".join(text.lower().split()).rstrip(".,;:!? ")


def declared_key(kind: str, representation: str, keystats: dict | None = None) -> str:
    """Key for a declared fact/mistake. A store that already tracks the legacy
    raw-text-hash key keeps using it so existing keystats/facts aren't orphaned."""
    if keystats is not None:
        legacy = f"{kind}:{hashlib.sha1(representation.strip().encode()).hexdigest()[:12]}"
        if legacy in keystats:
            return legacy
    return f"{kind}:{hashlib.sha1(normalize_rep(representation).encode()).hexdigest()[:12]}"


def cmd_observe_declared(args, store: Store, kind: str) -> int:
    representation = args.representation.strip()
    key = args.key or declared_key(kind, representation, store.load_keystats())
    if kind == "mistake":
        default_failure = FAILURE_COST_MISTAKE
    else:  # a declared fact/invariant is worth something even if never rediscovered
        default_failure = store.load_config()["fact_failure_cost"]
    failure_cost = args.failure_cost if args.failure_cost is not None else default_failure
    sources = [rel_to_root(s, store.repo_root) for s in (args.source or [])]
    obs = {
        "id": None,
        "ts": now(),
        "kind": kind,
        "key": key,
        "sources": sources,
        "task": None,
        "session": store.session,
        "cost": 0,
        "failure_cost": failure_cost,
        "representation": representation,
    }
    store.append_observation(obs)
    stats = update_keystats(store, key, kind, args.scope, sources, 0, failure_cost, representation)
    print(
        f"observed {kind}: {key} (access_count={stats['access_count']}, "
        f"sessions={stats['session_count']})"
    )
    print(f'representation: "{representation}"')
    return 0


# ---------------------------------------------------------------- candidates / promote / demote


def _active_fact_keys(facts: dict) -> set[str]:
    # includes stale facts: they still exist (awaiting confirm/demote), so
    # re-listing their key as a candidate would invite a duplicate promote
    return {f["key"] for f in facts["facts"].values()}


def _published_hot(facts: dict) -> list[dict]:
    """Hot facts that end up in AGENTS.md (incl. stale --critical ones)."""
    return sb_compile.active_facts(facts, "hot")


def build_candidates(store: Store, threshold: float, show_all: bool) -> list[dict]:
    keystats = store.load_keystats()
    facts = store.load_facts()
    config = store.load_config()
    kw = value_kwargs(config)
    t = now()
    active_keys = _active_fact_keys(facts)
    out = []
    for key, stats in keystats.items():
        if key in active_keys:
            continue
        pcost = candidate_persistent_cost(stats, estimate_tokens)
        value = compute_value(stats, pcost, now=t, **kw)
        if show_all or value >= threshold:
            out.append(
                {
                    "key": key,
                    "kind": stats["kind"],
                    "scope": stats.get("scope", "general"),
                    "access_count": stats["access_count"],
                    "distinct_sessions": distinct_sessions(stats),
                    "total_retrieval_cost": stats["total_retrieval_cost"],
                    "total_failure_cost": stats["total_failure_cost"],
                    "stability": stability(stats.get("hash_changes", 0)),
                    "decay": round(decay(stats.get("last_access"), kw["half_life_days"], t), 3),
                    "representation": stats.get("representation"),
                    "value": round(value, 3),
                }
            )
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
    print(f"{'value':>7}  {'sess':>4}  {'n':>3}  {'decay':>5}  {'kind':<8} {'scope':<12} key")
    for c in candidates:
        print(
            f"{c['value']:>7.2f}  {c['distinct_sessions']:>4}  {c['access_count']:>3}  "
            f"{c['decay']:>5.2f}  {c['kind']:<8} {c['scope']:<12} {c['key']}"
        )
        if c["representation"]:
            print(f"         representation: {c['representation']}")
        else:
            print(
                "         representation: (none yet -- supply one with `seedbank promote --representation`)"
            )
    return 0


def cmd_promote(args, store: Store) -> int:
    keystats = store.load_keystats()
    stats = keystats.get(args.key)
    if not stats:
        print(
            f"error: no observations for key '{args.key}' (see `seedbank candidates`)",
            file=sys.stderr,
        )
        return 1
    representation = args.representation or stats.get("representation")
    if not representation:
        print('error: no representation available -- pass --representation "..."', file=sys.stderr)
        return 1

    facts = store.load_facts()
    existing = [fid for fid, f in facts["facts"].items() if f["key"] == args.key]
    if existing and not args.force:
        print(
            f"error: key '{args.key}' already has a fact ({', '.join(existing)}); "
            f"`seedbank demote <id> --tier cold` it first, or re-run promote with --force to replace it",
            file=sys.stderr,
        )
        return 1
    scope = safe_scope(args.scope or stats.get("scope", "general"))
    if args.replace and args.replace not in facts["facts"]:
        print(f"error: --replace: no fact {args.replace}", file=sys.stderr)
        return 1
    clashes = [
        (fid, ov)
        for fid, ov in conflicts(representation, scope, facts, skip_keys={args.key})
        if fid != args.replace
    ]
    if clashes and not args.force:
        print(
            f"error: refusing to promote -- contradicts active fact(s) in scope '{scope}':",
            file=sys.stderr,
        )
        for fid, ov in clashes:
            print(
                f'  {fid} (overlap {ov:.2f}): "{facts["facts"][fid]["representation"]}"',
                file=sys.stderr,
            )
        print(
            f"re-run with --replace {clashes[0][0]} to retire the old fact, "
            f"or --force to keep both",
            file=sys.stderr,
        )
        return 1
    for fid in existing:  # --force: replace, never duplicate
        del facts["facts"][fid]
        print(f"replacing {fid}")
    if args.replace:
        del facts["facts"][args.replace]
        print(f"replaced {args.replace} (demoted to cold)")
    pcost = estimate_tokens(representation)
    tier = args.tier

    if tier == "hot":
        config = store.load_config()
        budget = args.budget if args.budget is not None else config["hot_budget"]
        current_hot = _published_hot(facts)
        current_total = sum(f["persistent_token_cost"] for f in current_hot)
        if current_total + pcost > budget:
            if pcost > budget:
                print(
                    f"error: this fact alone costs {pcost} tokens, more than the entire "
                    f"hot budget ({budget}) -- no amount of evicting existing hot facts "
                    f"can make room. Raise --budget or use --tier warm.",
                    file=sys.stderr,
                )
                return 1
            evicted = _evict_to_budget(
                facts, budget - pcost, protect_keys={args.key}, keystats=keystats, config=config
            )
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
    sources = list(stats.get("sources", []))
    source_hashes = {}  # hash now, not the (possibly older) hash from the last observe
    for s in sources:
        h = sha256_file(store.source_path(s))
        if h is not None:
            source_hashes[s] = h
    fact = {
        "id": fid,
        "key": args.key,
        "representation": representation,
        "scope": scope,
        "sources": sources,
        "source_hashes": source_hashes,
        "access_count_at_promotion": stats["access_count"],
        "session_count_at_promotion": distinct_sessions(stats),
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
    print(f'promoted {fid} [{tier}] scope={scope} cost={pcost}tok: "{representation}"')
    return 0


def fact_value(f: dict, keystats: dict | None = None, config: dict | None = None) -> float:
    """Current value of a promoted fact: its key's live keystats when still
    tracked (accesses keep accruing after promotion, e.g. via the hook),
    else the snapshot taken at promotion. Promoting/revalidating counts as
    an access for decay; --critical facts never decay."""
    stats = (keystats or {}).get(f["key"])
    if stats is None:
        stats = {
            "kind": f["key"].split(":", 1)[0],
            "access_count": f["access_count_at_promotion"],
            "session_count": f.get("session_count_at_promotion", f["access_count_at_promotion"]),
            "total_retrieval_cost": f["retrieval_cost_at_promotion"],
            "total_failure_cost": f["failure_cost_at_promotion"],
            "hash_changes": 0 if f["stability"] >= 1.0 else round(1.0 / f["stability"] - 1.0),
        }
    stats = dict(
        stats,
        last_access=max(
            stats.get("last_access") or 0, f.get("promoted_at") or 0, f.get("last_verified") or 0
        ),
    )
    return compute_value(
        stats,
        f["persistent_token_cost"],
        critical=bool(f.get("protected")),
        **value_kwargs(config or {}),
    )


def _evict_to_budget(
    facts: dict,
    target_max: int,
    protect_keys: set[str],
    keystats: dict | None = None,
    config: dict | None = None,
) -> list[str] | None:
    """Demote lowest-value non-protected hot facts (to warm) until hot total <= target_max.
    Returns list of demoted fact ids, or None if it can't fit (all remaining are protected)."""
    hot = _published_hot(facts)  # stale --critical facts are still published, so they count
    total = sum(f["persistent_token_cost"] for f in hot)
    if total <= target_max:
        return []

    evictable = sorted(
        (f for f in hot if not f["protected"] and f["key"] not in protect_keys),
        key=lambda f: fact_value(f, keystats, config),
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
            h = sha256_file(store.source_path(s))
            if h is not None:
                fact["source_hashes"][s] = h
        fact["stale"] = False
        fact["stale_reason"] = None
        fact["last_verified"] = now()
        fact["confidence"] = min(1.0, fact.get("confidence", 0.8) + 0.05)
        # a revalidated non-critical hot fact re-enters the published set
        config = store.load_config()
        evicted = _evict_to_budget(
            facts, config["hot_budget"], protect_keys=set(),
            keystats=store.load_keystats(), config=config,
        )
        store.save_facts(facts)
        print(
            f"revalidated {args.confirm}: stale cleared, hashes refreshed, confidence={fact['confidence']:.2f}"
        )
        for fid in evicted or []:
            print(f"evicted (demoted to warm) to stay within hot budget: {fid}")
        if evicted is None:
            print("warning: hot tier over budget and nothing evictable (all --critical)", file=sys.stderr)
        return 0

    changed = 0
    for fact in facts["facts"].values():
        if not fact["sources"]:
            continue  # policy-style fact with no backing file: nothing to check
        stale_reason = None
        for s, old_hash in fact["source_hashes"].items():
            new_hash = sha256_file(store.source_path(s))
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
    evicted = _evict_to_budget(
        facts, budget, protect_keys=set(), keystats=store.load_keystats(), config=config
    )
    if evicted:
        for fid in evicted:
            print(f"evicted (over hot budget): {fid} -> warm")
    elif evicted is None:
        print(
            "warning: hot tier over budget and nothing evictable (all --critical)", file=sys.stderr
        )

    removed_stale = 0
    if args.purge_stale:
        stale_ids = [fid for fid, f in facts["facts"].items() if f.get("stale")]
        for fid in stale_ids:
            del facts["facts"][fid]
        removed_stale = len(stale_ids)

    store.save_facts(facts)
    removed_obs = store.prune_observations(keep_per_key=args.keep_per_key)
    print(
        f"gc: {removed_obs} raw observation(s) pruned (aggregates preserved), {removed_stale} stale fact(s) purged"
    )
    return 0


# ---------------------------------------------------------------- compile


def cmd_compile(args, store: Store, repo_root: Path) -> int:
    cmd_invalidate(argparse.Namespace(confirm=None), store)
    facts = store.load_facts()
    targets = args.targets.split(",") if args.targets else ["AGENTS.md"]
    try:
        report = sb_compile.compile_outputs(facts, repo_root, targets, force=args.force)
    except sb_compile.CompileRefused as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"compiled {report['hot_fact_count']} hot fact(s) [{report['hot_tokens']} tok] "
        f"+ {report['warm_fact_count']} warm fact(s)"
    )
    for path in report["written"]:
        print(f"  wrote {path}")
    if "AGENTS.md" in targets:
        claude_md = repo_root / "CLAUDE.md"
        if not claude_md.is_file() or "@AGENTS.md" not in claude_md.read_text(errors="replace"):
            print("hint: Claude Code reads CLAUDE.md, not AGENTS.md -- add a line `@AGENTS.md` to CLAUDE.md")
    kept_stale = [
        f["id"] for f in facts["facts"].values() if f.get("stale") and f.get("protected")
    ]
    if kept_stale:
        print(
            f"kept {len(kept_stale)} stale --critical fact(s), marked 'verify': {', '.join(kept_stale)}",
            file=sys.stderr,
        )
    if report["stale_excluded"]:
        print(
            f"excluded {len(report['stale_excluded'])} stale fact(s) (not exposed): {', '.join(report['stale_excluded'])}",
            file=sys.stderr,
        )
    return 0


# ---------------------------------------------------------------- status / stats


def cmd_status(args, store: Store) -> int:
    keystats = store.load_keystats()
    facts = store.load_facts()["facts"]
    config = store.load_config()
    hot = [f for f in facts.values() if f["tier"] == "hot" and not f.get("stale")]
    warm = [f for f in facts.values() if f["tier"] == "warm" and not f.get("stale")]
    stale = [f for f in facts.values() if f.get("stale")]
    hot_tokens = sum(f["persistent_token_cost"] for f in _published_hot(store.load_facts()))
    candidates = build_candidates(store, config["promote_threshold"], show_all=False)

    print(f"tracked keys:        {len(keystats)}")
    print(
        f"active facts:        {len(facts)}  (hot={len(hot)}, warm={len(warm)}, stale={len(stale)})"
    )
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
        print(
            json.dumps(
                {
                    "total_tokens_avoided": total_avoided,
                    "facts": [
                        {"id": r[0], "tokens_avoided": r[1], "tier": r[2], "representation": r[3]}
                        for r in rows
                    ],
                },
                indent=2,
            )
        )
        return 0
    print(f"estimated tokens avoided so far: {total_avoided}")
    print("(one-time savings already realized by promoting: pre-promotion rediscovery cost")
    print(" minus the persistent cost of keeping the compact fact around; does not yet count")
    print(" future avoided rediscoveries)")
    for fid, avoided, tier, rep in rows:
        print(f"  {fid} [{tier}] ~{avoided} tok avoided: {rep[:70]}")
    return 0


# ---------------------------------------------------------------- import


_GEN_SCOPE_SUFFIX = re.compile(r"\s+\[([a-z0-9_-]+)\]$")
_GEN_CONF_SUFFIX = re.compile(r"\s+\(confidence=[0-9.]+\)$")
_GEN_WARM_POINTER = re.compile(r"^\*\*[^*]+\*\*: see `\.seedbank/warm/")


def cmd_import(args, store: Store) -> int:
    text = Path(args.file).read_text(errors="replace")
    keystats = store.load_keystats()
    known_reps = {
        " ".join(f["representation"].split()) for f in store.load_facts()["facts"].values()
    }
    # Our own compiled output (AGENTS.md block / warm/*.md): scopes come from
    # the "[scope]" suffix or "Warm context: <scope>" heading, not from the
    # generated section headings, and pointer lines aren't facts.
    generated = False
    scope = "general"
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == sb_compile.BEGIN or stripped.startswith("<!-- Generated by seedbank"):
            generated = True
            continue
        if stripped == sb_compile.END:
            generated = False
            scope = "general"
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading.startswith("Warm context: "):
                scope = safe_scope(heading[len("Warm context: ") :])
            elif not generated:
                scope = safe_scope(heading)
            continue
        if stripped.startswith(("- ", "* ")):
            rep = stripped[2:].strip()
            line_scope = scope
            if generated:
                if _GEN_WARM_POINTER.match(rep):
                    continue
                m = _GEN_SCOPE_SUFFIX.search(rep)
                if m:
                    line_scope = m.group(1)
                    rep = rep[: m.start()]
                rep = _GEN_CONF_SUFFIX.sub("", rep)
                if rep.endswith(sb_compile.STALE_MARK):
                    rep = rep[: -len(sb_compile.STALE_MARK)]
                rep = rep.strip()
            if not rep or rep in known_reps:
                continue
            key = f"imported:{hashlib.sha1(rep.encode()).hexdigest()[:12]}"
            if key in keystats:
                continue  # already imported (re-import / duplicate bullet)
            obs = {
                "id": None,
                "ts": now(),
                "kind": "imported",
                "key": key,
                "sources": [args.file],
                "task": None,
                "cost": 0,
                "failure_cost": 0,
                "representation": rep,
            }
            store.append_observation(obs)
            keystats[key] = update_keystats(store, key, "imported", line_scope, [], 0, 0, rep)
            count += 1
    print(
        f"imported {count} candidate bullet(s) from {args.file} (review with `seedbank candidates`, then promote)"
    )
    return 0


# ---------------------------------------------------------------- CLI wiring


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seedbank")
    p.add_argument("--store", default=None, help="override store directory (default: <git repo root>/.seedbank)")
    p.add_argument(
        "--session",
        default=None,
        help="session id for distinct-session counting (default: $CLAUDE_CODE_SESSION_ID, "
        "else date + terminal session)",
    )
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
    p_run.add_argument(
        "--timeout", type=float, default=600, help="kill the command after SECONDS (default 600)"
    )
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
    p_promote.add_argument(
        "--critical", action="store_true", help="protect from value-based eviction"
    )
    p_promote.add_argument("--budget", type=int, default=None)
    p_promote.add_argument(
        "--force",
        action="store_true",
        help="replace an existing fact for this key; keep both on a detected contradiction",
    )
    p_promote.add_argument(
        "--replace",
        default=None,
        metavar="FACT_ID",
        help="retire (demote to cold) this contradicting fact and promote the new one",
    )

    p_demote = sub.add_parser("demote", help="lower a fact's tier, or remove it (--tier cold)")
    p_demote.add_argument("fact_id")
    p_demote.add_argument("--tier", choices=["warm", "cold"], required=True)

    p_inval = sub.add_parser(
        "invalidate", help="scan facts for changed sources, or confirm one is still valid"
    )
    p_inval.add_argument("--confirm", default=None, metavar="FACT_ID")

    p_gc = sub.add_parser("gc", help="enforce hot budget, purge stale facts, prune observation log")
    p_gc.add_argument("--purge-stale", action="store_true")
    p_gc.add_argument("--keep-per-key", type=int, default=5)

    p_compile = sub.add_parser("compile", help="write AGENTS.md (+ warm/*.md) from active facts")
    p_compile.add_argument(
        "--targets", default=None, help="comma-separated output filenames (default: AGENTS.md)"
    )
    p_compile.add_argument(
        "--force",
        action="store_true",
        help="compile even if the store is empty and would wipe an existing seedbank block",
    )

    sub.add_parser("status", help="quick health summary")

    sub.add_parser(
        "hook", help="Claude Code PostToolUse hook entry point (reads hook JSON on stdin)"
    )

    p_stats = sub.add_parser("stats", help="report estimated tokens avoided")
    p_stats.add_argument("--json", action="store_true")

    p_import = sub.add_parser(
        "import", help="seed candidates from an existing AGENTS.md/CLAUDE.md-style file"
    )
    p_import.add_argument("file")

    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:  # fast path: no argparse, never fails the tool call
        import sb_hook

        return sb_hook.main()
    args = build_parser().parse_args(argv)
    repo_root = find_repo_root()
    store = Store(find_store_root(args.store, repo_root), repo_root)
    store.session = resolve_session(args.session)

    if args.cmd == "observe" and args.obs_kind == "run":
        return cmd_observe_run(args, store)  # locks only around its bookkeeping
    with store.lock():
        return _dispatch(args, store, repo_root)


def _dispatch(args, store: Store, repo_root: Path) -> int:
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
        return cmd_compile(args, store, repo_root)
    try:
        return top_handlers[args.cmd](args, store)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

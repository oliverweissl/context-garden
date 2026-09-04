"""Symbol-level call/reference graph BFS, with a weaker file-import fallback
for pairs that share no direct symbol edge (e.g. a type used only via a
Python annotation without a matching AST Call, or any other reference the
lightweight indexer's name resolution didn't catch) but do live in files
that import one another.

Optionally merges in externally supplied graph edges (e.g. from Graphify or
an equivalent tool) -- see `merge_external_graph`. This is additive only;
pruner never requires an external graph to function.
"""
from __future__ import annotations

from collections import deque

SAME_FILE_DISTANCE = 2
IMPORT_LINKED_DISTANCE = 3


def build_adjacency(call_edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in call_edges:
        adj.setdefault(e["from"], set()).add(e["to"])
        adj.setdefault(e["to"], set()).add(e["from"])
    return adj


def merge_external_graph(adj: dict[str, set[str]], external: dict) -> None:
    """`external` follows {"nodes": [...], "edges": [{"from":, "to":}, ...]}.
    Unknown node ids (not present in this repo's symbol table) are kept --
    they simply won't match anything and are harmless -- rather than
    rejected, so a partially-applicable external graph still helps."""
    for e in external.get("edges", []):
        a, b = e.get("from"), e.get("to")
        if a and b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)


def bfs_distances(adj: dict[str, set[str]], seeds: set[str], max_depth: int = 6) -> dict[str, int]:
    dist = {s: 0 for s in seeds}
    q = deque(seeds)
    while q:
        cur = q.popleft()
        if dist[cur] >= max_depth:
            continue
        for nxt in adj.get(cur, ()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def file_of(symbol_id: str) -> str:
    return symbol_id.split(":", 1)[0]


def all_distances(index: dict, seeds: set[str]) -> dict[str, int]:
    """Symbol-graph BFS distance for every symbol, falling back to a fixed
    same-file / import-linked distance for symbols the call graph never
    connects to a seed at all."""
    adj = build_adjacency(index["call_edges"])
    dist = bfs_distances(adj, seeds)

    seed_files = {file_of(s) for s in seeds}
    for rel, entry in index["files"].items():
        related = rel in seed_files or bool(set(entry.get("resolved_imports", [])) & seed_files)
        if not related:
            for sf in seed_files:
                if rel in index["files"].get(sf, {}).get("resolved_imports", []):
                    related = True
                    break
        if not related:
            continue
        fallback = SAME_FILE_DISTANCE if rel in seed_files else IMPORT_LINKED_DISTANCE
        for sym in entry["symbols"]:
            sid = sym["id"]
            if sid not in dist or dist[sid] > fallback:
                dist[sid] = fallback
    return dist

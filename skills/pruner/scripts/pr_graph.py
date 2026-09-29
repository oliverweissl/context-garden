"""Symbol-level BFS over the lazily resolved reference edges
(pr_store.Index.neighbors), plus optional additive external graph edges.
No flat same-file/import-linked fallback score: it credited every neighbouring
symbol regardless of relevance and did not help the eval.
"""

from __future__ import annotations

from collections import deque

MAX_DEPTH = 5  # graph_score is 0 at >= 6 hops, so never explore further
MAX_VISITED = 20_000  # hard cap on BFS size for very large repos


def merge_external_graph(external: dict) -> dict[str, set[str]]:
    """`external` follows {"nodes": [...], "edges": [{"from":, "to":}, ...]}.
    Unknown node ids (not in this repo's symbol table) are kept -- they
    simply match nothing -- so a partially-applicable graph still helps."""
    adj: dict[str, set[str]] = {}
    for e in external.get("edges", []):
        a, b = e.get("from"), e.get("to")
        if a and b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return adj


def bfs_distances(index, seeds: set[str], extra_adj: dict | None = None) -> dict[str, int]:
    extra_adj = extra_adj or {}
    dist = {s: 0 for s in seeds}
    q = deque(seeds)
    while q and len(dist) < MAX_VISITED:
        cur = q.popleft()
        if dist[cur] >= MAX_DEPTH:
            continue
        for nxt in index.neighbors(cur) | extra_adj.get(cur, set()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def file_of(symbol_id: str) -> str:
    return symbol_id.split(":", 1)[0]

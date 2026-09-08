"""Deterministic event normalization and clustering.

No LLM involved: repeated events are collapsed purely by regex-based
normalization of their message text, so identical failures collapse to one
group regardless of which line/iteration/hex-address made them unique.
"""

from __future__ import annotations

import re

_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def normalize(message: str) -> str:
    s = message.strip()
    s = _HEX_RE.sub("0xN", s)
    s = _NUM_RE.sub("#", s)
    s = _WS_RE.sub(" ", s)
    return s


def cluster_events(events: list[dict]) -> list[dict]:
    """Group events with the same normalized signature.

    Returns a list of {signature, representative, count, members} in first-seen
    order. `representative` is the first raw event in the group (earliest
    occurrence), so its original, unnormalized message is preserved for display.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    for ev in events:
        sig = normalize(ev["message"])
        if sig not in groups:
            groups[sig] = {
                "signature": sig,
                "representative": ev,
                "count": 0,
                "members": [],
            }
            order.append(sig)
        groups[sig]["count"] += 1
        groups[sig]["members"].append(ev)
    return [groups[s] for s in order]


def compact_ranges(nums: list[int]) -> str:
    """[1200, 1201, 1202, 1830] -> '1200-1202,1830'"""
    if not nums:
        return ""
    nums = sorted(set(nums))
    ranges = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ranges)

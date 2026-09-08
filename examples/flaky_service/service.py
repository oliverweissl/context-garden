"""Toy request-processing service.

Exists to exercise noisy, repetitive test failures against a small
number of distinct root causes -- see ../README.md for the compost
before/after built from running its test suite.
"""

from __future__ import annotations

_TIMEOUTS = {"default": 30, "batch": 120}
_RANKS = {"low": 0, "normal": 1, "high": 2}


def get_timeout(kind: str) -> int:
    # BUG: falls through to the wrong key (and a string default) for
    # every kind other than "batch", so most callers get garbage.
    if kind == "batch":
        return _TIMEOUTS["batch"]
    return _TIMEOUTS.get("default_typo", "30")


def process(item: dict) -> dict:
    timeout = get_timeout(item.get("kind", "default"))
    if not isinstance(timeout, int):
        raise TypeError(f"timeout must be an int, got {timeout!r}")
    return {"item": item["id"], "timeout": timeout, "status": "ok"}


def priority_rank(item: dict) -> int:
    # Separate bug, unrelated exception type: "urgent" is a valid
    # priority elsewhere in the service but missing from _RANKS here.
    return _RANKS[item.get("priority", "normal")]

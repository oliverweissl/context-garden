"""Event normalization, clustering and root-error selection.

Only volatile tokens are normalized (small integers kept); see
references/schema.md "Clustering".
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"
)
_TMP_RE = re.compile(
    r"(?:/private)?/(?:tmp|var/folders|var/tmp)/\S*"
    r"|[A-Za-z]:\\\S*?\\Temp\\\S*"
    r"|\bpytest-of-[^/\s]+/pytest-\d+\S*"
)
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_PID_RE = re.compile(r"\b(pid|tid)(\s*[=:#]?\s*)\d+", re.IGNORECASE)
_SYSLOG_PID_RE = re.compile(r"\b([A-Za-z][\w.-]*)\[\d+\]:")
_LONG_NUM_RE = re.compile(r"\d{6,}")
_WS_RE = re.compile(r"\s+")


def normalize(message: str) -> str:
    s = message.strip()
    s = _TS_RE.sub("<TS>", s)
    s = _TMP_RE.sub("<TMP>", s)
    s = _HEX_RE.sub("0xN", s)
    s = _PID_RE.sub(r"\1\2<PID>", s)
    s = _SYSLOG_PID_RE.sub(r"\1[<PID>]:", s)
    s = _LONG_NUM_RE.sub("<N>", s)
    s = _WS_RE.sub(" ", s)
    return s


def event_signature(ev: dict) -> str:
    """Cluster key. Compiler diagnostics: file + normalized message (line:col
    dropped). Everything else: the normalized message."""
    if ev.get("file") and ev.get("text") is not None:
        return f"{ev['file']}: {normalize(ev['text'])}"
    return normalize(ev["message"])


def cluster_events(events: list) -> list:
    """Group events with the same signature, in first-seen order.

    Each group is {signature, representative, count, lines, tests,
    user_location}; `representative` is the earliest raw event, so its
    original, unnormalized message is preserved for display. Member events
    themselves are not retained.
    """
    groups: dict = {}
    order: list = []
    for ev in events:
        sig = event_signature(ev)
        g = groups.get(sig)
        if g is None:
            g = groups[sig] = {
                "signature": sig,
                "representative": ev,
                "count": 0,
                "lines": [],
                "tests": set(),
            }
            order.append(sig)
        g["count"] += 1
        g["lines"].append(ev["line"])
        if ev.get("test"):
            g["tests"].add(ev["test"])
    return [groups[s] for s in order]


# ---------------------------------------------------------------- user-owned files

_SYSTEM_PREFIXES = (
    "/usr/", "/opt/", "/Library/", "/Applications/", "/System/", "/nix/",
    "/sw/", "/Developer/", "/private/var/",
)
_SDK_MARKERS = (".sdk/", ".xctoolchain/", "/Xcode.app/", "/CommandLineTools/", "/Toolchains/")
_VENDOR_PARTS = {
    "vendor", "third_party", "third-party", "thirdparty", "build", "_deps",
    "site-packages", "dist-packages", ".venv", "venv", "node_modules",
}


def is_user_path(path: str, root) -> bool:
    """True if `path` (as printed by a tool) is a file the user owns: inside
    the repo root (relative paths are taken as repo-relative), not under a
    system include dir / SDK, and not in a vendored or build directory."""
    if not path or path.startswith("<"):
        return False
    p = path.replace("\\", "/")
    if any(m in p for m in _SDK_MARKERS):
        return False
    root_s = str(Path(root)).rstrip("/") or "/"
    if os.path.isabs(p):
        p = os.path.normpath(p)
        if root_s != "/" and not (p == root_s or p.startswith(root_s + "/")):
            return False
        if any(p.startswith(s) for s in _SYSTEM_PREFIXES) and not any(
            (root_s + "/").startswith(s) for s in _SYSTEM_PREFIXES
        ):
            return False
        rel = p[len(root_s):].lstrip("/")
    else:
        rel = os.path.normpath(p)
    parts = [x for x in rel.split("/") if x not in ("", ".", "..")]
    return bool(parts) and not any(x in _VENDOR_PARTS for x in parts[:-1])


def user_location(ev: dict, root) -> str | None:
    """User-code location attached to an event from a non-user file: the
    first instantiation-backtrace location in user code, else the first
    user-owned include site."""
    ctx = ev.get("context") or []
    for kind in ("instantiation", "include"):
        for c in ctx:
            if c.get("kind") == kind and is_user_path(c["file"], root):
                return c["loc"]
    return None


def pick_root_error(errors: list, root):
    """First error (in output order) located in a user-owned file; else the
    first error with a user-code instantiation/include location; else the
    first error overall. `errors` must be sorted by line."""
    for ev in errors:
        if ev.get("file") and is_user_path(ev["file"], root):
            return ev
    for ev in errors:
        if user_location(ev, root):
            return ev
    return errors[0] if errors else None


def compact_ranges(nums: list[int], max_ranges: int = 20) -> str:
    """[1200, 1201, 1202, 1830] -> '1200-1202,1830' (first max_ranges, then '+N more')"""
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
    out = ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ranges[:max_ranges])
    if len(ranges) > max_ranges:
        out += f",+{len(ranges) - max_ranges} more"
    return out

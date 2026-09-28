"""Token estimation. Offline, always: no tokenizer download, no API call.

Default is the suite-wide chars/4 heuristic (see compost/seedbank/pruner).
If references/token-calibration.json exists (written by the repo's
`scripts/calibrate-tokens`, which the user runs manually against their own
`claude` CLI), its measured chars-per-token ratio is used per content
category instead:

- ``yaml``     -- frontmatter (name/description)
- ``code``     -- fenced code blocks inside markdown, or whole code files
- ``markdown`` -- everything else (prose + markdown structure)

Every number this module produces is still an ESTIMATE; `estimate_label()`
says which kind, and `error_range_pct()` gives the calibration's observed
per-sample error so reports never present an estimate as a measurement.
`$WEEDER_TOKEN_CALIBRATION` overrides the calibration file path (used by
the smoke test; point it at a nonexistent path to force chars/4).
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_CHARS_PER_TOKEN = 4.0
CATEGORIES = ("markdown", "code", "yaml")
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "references" / "token-calibration.json"
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)


def calibration_path() -> Path:
    override = os.environ.get("WEEDER_TOKEN_CALIBRATION")
    return Path(override) if override else _DEFAULT_PATH


@lru_cache(maxsize=4)
def _load(path_str: str) -> dict | None:
    try:
        data = json.loads(Path(path_str).read_text())
    except (OSError, ValueError):
        return None
    cats = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(cats, dict):
        return None
    return data


def load_calibration() -> dict | None:
    return _load(str(calibration_path()))


def chars_per_token(category: str) -> float:
    cal = load_calibration()
    if cal:
        ratio = (cal["categories"].get(category) or {}).get("chars_per_token")
        if isinstance(ratio, (int, float)) and ratio > 0:
            return float(ratio)
    return DEFAULT_CHARS_PER_TOKEN


def _count(chars: int, category: str) -> float:
    return chars / chars_per_token(category) if chars else 0.0


def estimate_tokens(text: str, category: str = "markdown") -> int:
    """`category="markdown"` splits fenced code blocks out and estimates
    them at the ``code`` ratio; ``code``/``yaml`` apply one ratio to the
    whole text."""
    if not text:
        return 0
    if category == "markdown":
        code_chars = sum(len(m.group(0)) for m in _FENCE_RE.finditer(text))
        total = _count(len(text) - code_chars, "markdown") + _count(code_chars, "code")
    else:
        total = _count(len(text), category)
    return max(1, round(total))


def estimate_label() -> str:
    cal = load_calibration()
    if cal:
        return f"estimated (calibrated {cal.get('date', 'unknown date')})"
    return "estimated (uncalibrated chars/4)"


def error_range_pct() -> tuple[float, float] | None:
    """(min, max) signed per-sample error of the calibrated estimate vs the
    real count observed during calibration, in percent. None if
    uncalibrated (chars/4 has no measured error bound here)."""
    cal = load_calibration()
    rng = (cal or {}).get("overall_error_range_pct")
    if isinstance(rng, list) and len(rng) == 2:
        return float(rng[0]), float(rng[1])
    return None


def estimate_note() -> str:
    """One line for report headers."""
    rng = error_range_pct()
    if rng is None:
        return (
            f"Token counts: {estimate_label()}; "
            "measure with the context-garden repo's scripts/calibrate-tokens."
        )
    return (
        f"Token counts: {estimate_label()}; observed per-sample error "
        f"{rng[0]:+.1f}% to {rng[1]:+.1f}%."
    )


def estimate_info() -> dict:
    """Machine-readable form of estimate_note() for --json output."""
    cal = load_calibration()
    return {
        "label": estimate_label(),
        "calibrated": cal is not None,
        "calibration_date": (cal or {}).get("date"),
        "chars_per_token": {c: chars_per_token(c) for c in CATEGORIES},
        "error_range_pct": list(error_range_pct()) if error_range_pct() else None,
    }

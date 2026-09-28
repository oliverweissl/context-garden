"""Spec lock (.trellis/spec.lock) and per-commit baselines
(.trellis/baselines/<sha>.json).

The lock pins each check's *strictness configuration* (CheckResult.config:
expected orders, tolerances, alpha, resolutions, seeds, reference
fingerprints). `trellis run` compares the current spec against it and
FAILs if anything got looser -- so a gate cannot be quietly relaxed to
make it pass; changing the lock is a reviewed, committed human decision.

Baselines store the *measured metrics* per check for a commit, so a later
run can FAIL on regression (observed order dropped, error/residual grew).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ._util import fingerprints_equal

LOCK_VERSION = 1

# how each config key orders strictness
LARGER_IS_LOOSER = {
    "tol", "tol_order", "rtol", "atol", "tol_spread", "floor_rtol", "warn_threshold",
    "fail_threshold", "confidence", "fd_safety", "f_rtol",
}
# (alpha: every trellis test FAILs only on rejection, so a *smaller* alpha
# rejects less often = looser; likewise fewer samples/runs = less power)
SMALLER_IS_LOOSER = {"expected_order", "alpha", "n_runs", "n_samples", "cond_provided"}
MUST_MATCH = {
    "reference", "expected", "expected_mean", "expected_std", "expected_range", "resolutions",
    "dts", "param_values", "seeds", "param_kind", "relative", "ord", "direction", "assume_normal",
}


# --------------------------------------------------------------------------
# git / paths
# --------------------------------------------------------------------------


def _git(args, cwd) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_root(path: Path) -> Path | None:
    r = _git(["rev-parse", "--show-toplevel"], Path(path).resolve().parent)
    return Path(r) if r else None


def git_sha(root: Path) -> tuple[str | None, bool]:
    sha = _git(["rev-parse", "HEAD"], root)
    dirty = bool(_git(["status", "--porcelain", "--untracked-files=no"], root))
    return sha, dirty


def default_trellis_dir(spec_path: Path) -> Path:
    root = git_root(spec_path)
    return (root if root else Path(spec_path).resolve().parent) / ".trellis"


def spec_key(spec_path: Path) -> str:
    spec_path = Path(spec_path).resolve()
    root = git_root(spec_path)
    if root:
        try:
            return spec_path.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return spec_path.name


def check_keys(checks) -> list[str]:
    """Unique names per check (duplicates get '#2', '#3', ...)."""
    seen, keys = {}, []
    for c in checks:
        seen[c.name] = seen.get(c.name, 0) + 1
        keys.append(c.name if seen[c.name] == 1 else f"{c.name}#{seen[c.name]}")
    return keys


# --------------------------------------------------------------------------
# lock
# --------------------------------------------------------------------------


def lock_entry(checks) -> dict:
    return {
        "locked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": {k: {"category": c.category, "config": c.config} for k, c in zip(check_keys(checks), checks)},
    }


def read_lock(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"version": LOCK_VERSION, "specs": {}}
    return json.loads(path.read_text())


def write_lock(path: Path, spec: str, entry: dict) -> Path:
    path = Path(path)
    data = read_lock(path)
    data.setdefault("specs", {})[spec] = entry
    data["version"] = LOCK_VERSION
    data["specs"] = dict(sorted(data["specs"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    return path


def _fmt(v) -> str:
    if isinstance(v, dict) and "sha256" in v:
        return f"<data sha256:{v['sha256'][:12]}>"
    s = json.dumps(v)
    return s if len(s) <= 80 else s[:77] + "..."


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict) and "sha256" in a and "sha256" in b:
        return fingerprints_equal(a, b)
    return a == b


def _num(v):
    return v if isinstance(v, (int, float)) else None  # bool counts (False < True)


def compare_to_lock(locked: dict, current: dict) -> dict:
    """-> {"looser": [...], "tighter": [...], "changed_info": [...], "added": [...]}
    (lists of human-readable diff lines). Any 'looser' line = FAIL."""
    out = {"looser": [], "tighter": [], "changed_info": [], "added": []}
    lc, cc = locked.get("checks", {}), current.get("checks", {})
    for name in lc:
        if name not in cc:
            out["looser"].append(f"{name}: check REMOVED from the spec")
    for name in cc:
        if name not in lc:
            out["added"].append(f"{name}: new check (not in lock)")
    for name in lc:
        if name not in cc:
            continue
        old, new = lc[name].get("config", {}), cc[name].get("config", {})
        for key in sorted(set(old) | set(new)):
            a, b = old.get(key), new.get(key)
            if _same(a, b):
                continue
            line = f"{name}: {key} {_fmt(a)} -> {_fmt(b)}"
            if key in LARGER_IS_LOOSER or key in SMALLER_IS_LOOSER:
                if a is None and b is not None:
                    out["tighter"].append(line)
                    continue
                if b is None or _num(a) is None or _num(b) is None:
                    out["looser"].append(line + " (no longer enforced)" if b is None else line)
                    continue
                looser = (b > a) if key in LARGER_IS_LOOSER else (b < a)
                out["looser" if looser else "tighter"].append(line)
            elif key in MUST_MATCH:
                if a is None:
                    out["tighter"].append(line + " (newly pinned)")
                else:
                    out["looser"].append(line + " (changed: must match the lock)")
            else:
                out["changed_info"].append(line)
    return out


# --------------------------------------------------------------------------
# baselines
# --------------------------------------------------------------------------

# metrics where larger = worse; a regression is cur > factor * base (and
# above a round-off floor so noise at ~1e-16 never trips it)
ERROR_METRICS = (
    "finest_error", "relative_residual", "residual_norm", "forward_error_bound", "relative_error",
    "max_relative_drift", "max_relative_error", "max_abs_error", "max_violation", "max_asymmetry",
    "spread", "residual", "error",
)
REGRESSION_FLOOR = 1e-13


def save_baseline(tdir: Path, sha: str, dirty: bool, spec: str, checks) -> Path:
    path = Path(tdir) / "baselines" / f"{sha}.json"
    data = json.loads(path.read_text()) if path.exists() else {"sha": sha, "specs": {}}
    data["specs"][spec] = {
        "saved_at": time.time(),
        "dirty": dirty,
        "checks": {
            k: {"status": c.status, "metric": c.metric, "config": c.config}
            for k, c in zip(check_keys(checks), checks)
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def load_baseline(tdir: Path, ref: str, spec: str) -> tuple[str, dict]:
    bdir = Path(tdir) / "baselines"
    files = sorted(bdir.glob("*.json")) if bdir.exists() else []
    cands = []
    for f in files:
        if ref != "latest" and not f.stem.startswith(ref):
            continue
        d = json.loads(f.read_text())
        if spec in d.get("specs", {}):
            cands.append((d["specs"][spec].get("saved_at", 0), f.stem, d["specs"][spec]))
    if not cands:
        raise FileNotFoundError(f"no baseline {ref!r} for spec {spec} under {bdir}")
    if ref != "latest" and len({c[1] for c in cands}) > 1:
        raise ValueError(f"ambiguous baseline prefix {ref!r}: {sorted({c[1] for c in cands})}")
    cands.sort()
    _, sha, entry = cands[-1]
    return sha, entry


def compare_baseline(base: dict, checks, factor: float = 2.0) -> dict:
    """-> {"regressions": [...], "notes": [...]}"""
    regs, notes = [], []
    bc = base.get("checks", {})
    for key, c in zip(check_keys(checks), checks):
        if key not in bc:
            notes.append(f"{key}: not in baseline")
            continue
        bm, cm = bc[key].get("metric", {}), c.metric
        bo, co = bm.get("observed_order"), cm.get("observed_order")
        tol_order = (c.config or {}).get("tol_order", 0.3)
        if isinstance(bo, (int, float)) and "observed_order" in cm:
            if co is None:
                regs.append(f"{key}: observed order {bo:.3g} -> not computable")
            elif bo - co > tol_order:
                regs.append(f"{key}: observed order dropped {bo:.3g} -> {co:.3g} (> tol_order={tol_order})")
        for m in ERROR_METRICS:
            b, v = bm.get(m), cm.get(m)
            if not isinstance(b, (int, float)) or not isinstance(v, (int, float)) or isinstance(b, bool):
                continue
            if v != v or (v > factor * b and v > REGRESSION_FLOOR):
                regs.append(f"{key}: {m} grew {b:.3g} -> {v:.3g} (> {factor}x baseline)")
        if bc[key].get("status") == "PASS" and c.status != "PASS":
            notes.append(f"{key}: status PASS -> {c.status}")
    for key in bc:
        if key not in check_keys(checks):
            notes.append(f"{key}: in baseline but not run now")
    return {"regressions": regs, "notes": notes}

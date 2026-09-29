"""Persist benchmark records and summarize them.

records.jsonl holds one BenchmarkRecord.to_dict() per line (see
types.py). `analyze` turns it into per-(task, arm) statistics, written
as summary.json (machine-readable; what `scripts/benchmark plot` reads)
and summary.md (human-readable). Both can be regenerated at any time
with `scripts/benchmark analyze <results-dir>`.

Primary metric: verified pass rate per task x arm, with a Wilson 95% CI.
Secondary: total tokens and cost (mean with t-based 95% CI), deltas vs
baseline (Welch t CI for means, Newcombe hybrid-score CI for pass rates),
and tokens split by whether the task's skill was actually invoked.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .types import ARMS, BenchmarkRecord

Z95 = 1.959963984540054
# Two-sided 95% Student-t critical values for df = 1..30.
_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)  # fmt: skip

NATURAL_ARMS = ("treatment-natural", "treatment")  # "treatment": legacy records


# --------------------------------------------------------------------------- io


def write_records(records: Iterable[BenchmarkRecord], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.to_dict()) + "\n")
    return path


def load_records(path: Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # truncated trailing line from an interrupted run
    return rows


# ------------------------------------------------------------------------ stats


def t_crit(df: float) -> float:
    """Two-sided 95% Student-t critical value (table up to 30 df, then a
    Cornish-Fisher expansion around the normal quantile)."""
    if df <= 0 or math.isnan(df):
        return float("nan")
    if df <= 30 and float(df).is_integer():
        return _T95[int(df) - 1]
    if df < 30:
        lo, hi = math.floor(df), math.ceil(df)
        a, b = _T95[max(lo, 1) - 1], _T95[hi - 1]
        return a + (b - a) * (df - lo)
    z = Z95
    return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float, float]:
    """(p_hat, lo, hi) Wilson score interval for k successes in n trials."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def mean_ci(xs: Sequence[float]) -> tuple[float, float, float]:
    """(mean, lo, hi) t-based 95% CI; lo/hi are NaN for n < 2."""
    n = len(xs)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    m = statistics.fmean(xs)
    if n < 2:
        return m, float("nan"), float("nan")
    half = t_crit(n - 1) * statistics.stdev(xs) / math.sqrt(n)
    return m, m - half, m + half


def welch_diff_ci(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float]:
    """(mean(xs) - mean(ys), lo, hi) Welch 95% CI."""
    nx, ny = len(xs), len(ys)
    if nx == 0 or ny == 0:
        return float("nan"), float("nan"), float("nan")
    d = statistics.fmean(xs) - statistics.fmean(ys)
    if nx < 2 or ny < 2:
        return d, float("nan"), float("nan")
    vx, vy = statistics.variance(xs) / nx, statistics.variance(ys) / ny
    se2 = vx + vy
    if se2 == 0:
        return d, d, d
    df = se2**2 / (vx**2 / (nx - 1) + vy**2 / (ny - 1))
    half = t_crit(df) * math.sqrt(se2)
    return d, d - half, d + half


def newcombe_diff_ci(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float, float]:
    """(p1 - p2, lo, hi) Newcombe hybrid-score 95% CI (method 10)."""
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan"), float("nan")
    p1, l1, u1 = wilson(k1, n1)
    p2, l2, u2 = wilson(k2, n2)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


# --------------------------------------------------------------------- analysis


def _total_tokens(row: dict[str, Any]) -> int:
    if "total_tokens" in row:
        return row["total_tokens"]
    return sum(
        row.get(k, 0)
        for k in (
            "input_tokens",
            "repository_tokens",
            "tool_result_tokens",
            "skill_tokens",
            "output_tokens",
        )
    )


def _is_infra_error(row: dict[str, Any]) -> bool:
    """A trial where the model never ran. Records from before the
    `infra_error` field existed are recognised by zero model tokens."""
    if row.get("infra_error"):
        return True
    return not row.get("input_tokens") and not row.get("output_tokens")


def _invoked(row: dict[str, Any], component: str) -> bool:
    # Plugin-installed skills are reported as "<plugin>:<name>".
    return any(name.split(":")[-1] == component for name in row.get("skill_invoked") or [])


def _clean(x: float | None) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def _triple(t: tuple[float, float, float]) -> dict[str, float | None]:
    return {"value": _clean(t[0]), "lo": _clean(t[1]), "hi": _clean(t[2])}


def _arm_order(arms: Iterable[str]) -> list[str]:
    arms = set(arms)
    return [a for a in ARMS if a in arms] + sorted(arms - set(ARMS))


def _arm_stats(rows: list[dict[str, Any]], component: str) -> dict[str, Any]:
    n = len(rows)
    passes = sum(bool(r.get("verification_success")) for r in rows)
    tokens = [_total_tokens(r) for r in rows]
    costs = [float(r.get("cost_usd", 0.0) or 0.0) for r in rows]
    recorded = [r for r in rows if "skill_invoked" in r]
    inv = [r for r in recorded if _invoked(r, component)]
    not_inv = [r for r in recorded if not _invoked(r, component)]
    return {
        "n": n,
        "passes": passes,
        "pass_rate": _triple(wilson(passes, n)),
        "tokens": _triple(mean_ci(tokens)),
        "cost_usd": _triple(mean_ci(costs)),
        "invoked": len(inv) if recorded else None,
        "invoked_n": len(recorded),
        "invocation_rate": _triple(wilson(len(inv), len(recorded))) if recorded else None,
        "tokens_invoked": {"n": len(inv), **_triple(mean_ci([_total_tokens(r) for r in inv]))},
        "tokens_not_invoked": {
            "n": len(not_inv),
            **_triple(mean_ci([_total_tokens(r) for r in not_inv])),
        },
        "_tokens": tokens,
        "_costs": costs,
    }


def analyze(records: Iterable[dict[str, Any] | BenchmarkRecord]) -> dict[str, Any]:
    """Per-(task, arm) statistics; see module docstring for the metrics."""
    all_rows = [r.to_dict() if isinstance(r, BenchmarkRecord) else r for r in records]
    rows = [r for r in all_rows if not _is_infra_error(r)]
    by_task: dict[str, dict[str, list[dict[str, Any]]]] = {}
    components: dict[str, str] = {}
    for row in rows:
        by_task.setdefault(row["task_id"], {}).setdefault(row["condition"], []).append(row)
        components[row["task_id"]] = row.get("component", "")

    seeds = sorted({r["seed"] for r in rows if r.get("seed") is not None})
    tasks = []
    for task_id in sorted(by_task):
        component = components[task_id]
        arms = {a: _arm_stats(by_task[task_id][a], component) for a in by_task[task_id]}
        base = arms.get("baseline")
        for arm, s in arms.items():
            if base is None or arm == "baseline":
                s["delta_vs_baseline"] = None
                continue
            tok = welch_diff_ci(s["_tokens"], base["_tokens"])
            base_mean = statistics.fmean(base["_tokens"]) if base["_tokens"] else 0
            pct = tuple(100 * x / base_mean for x in tok) if base_mean else (float("nan"),) * 3
            s["delta_vs_baseline"] = {
                "pass_rate": _triple(
                    newcombe_diff_ci(s["passes"], s["n"], base["passes"], base["n"])
                ),
                "tokens": _triple(tok),
                "tokens_pct": _triple(pct),
                "cost_usd": _triple(welch_diff_ci(s["_costs"], base["_costs"])),
            }
        for s in arms.values():
            del s["_tokens"], s["_costs"]
        tasks.append(
            {
                "task_id": task_id,
                "component": component,
                "arms": {a: arms[a] for a in _arm_order(arms)},
            }
        )
    return {
        "n_records": len(rows),
        "n_infra_errors": len(all_rows) - len(rows),
        "seeds": seeds,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------- render


def _fmt_ci(t: dict[str, Any] | None, fmt: str = "{:.0f}", pct: bool = False) -> str:
    if not t or t.get("value") is None:
        return "n/a"
    f = (lambda x: f"{100 * x:.0f}%") if pct else fmt.format
    if t.get("lo") is None:
        return f(t["value"])
    return f"{f(t['value'])} [{f(t['lo'])}, {f(t['hi'])}]"


def _fmt_delta(t: dict[str, Any] | None, fmt: str = "{:.0f}", pct: bool = False) -> str:
    if not t or t.get("value") is None:
        return "—"
    text = _fmt_ci(t, fmt, pct=pct)
    return text if t["value"] < 0 else "+" + text


def render_markdown(analysis: dict[str, Any]) -> str:
    if not analysis["tasks"]:
        return "No records.\n"
    seeds = ", ".join(str(s) for s in analysis["seeds"]) or "n/a"
    out = [
        f"# Benchmark summary\n\n{analysis['n_records']} record(s)"
        f" ({analysis.get('n_infra_errors', 0)} infra-error trial(s) excluded);"
        f" schedule seed(s): {seeds}. "
        "Intervals are 95% CIs (pass rate: Wilson; pass-rate delta: Newcombe; "
        "means and mean deltas: t / Welch t).\n",
        "## Primary: verified pass rate\n",
        "| task | arm | n | pass | pass rate [95% CI] | Δ vs baseline [95% CI] | skill invoked |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for task in analysis["tasks"]:
        for arm, s in task["arms"].items():
            d = s["delta_vs_baseline"]
            inv = "n/a" if s["invoked"] is None else f"{s['invoked']}/{s['invoked_n']}"
            out.append(
                f"| {task['task_id']} | {arm} | {s['n']} | {s['passes']}/{s['n']} | "
                f"{_fmt_ci(s['pass_rate'], pct=True)} | "
                f"{_fmt_delta(d and d['pass_rate'], pct=True)} | {inv} |"
            )

    out += [
        "\n## Secondary: tokens and cost\n",
        "| task | arm | tokens mean [95% CI] | Δ tokens vs baseline [95% CI] | Δ % | "
        "cost $ mean [95% CI] | tokens, skill invoked | tokens, not invoked |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for task in analysis["tasks"]:
        for arm, s in task["arms"].items():
            d = s["delta_vs_baseline"]
            ti, tn = s["tokens_invoked"], s["tokens_not_invoked"]
            out.append(
                f"| {task['task_id']} | {arm} | {_fmt_ci(s['tokens'])} | "
                f"{_fmt_delta(d and d['tokens'], '{:.0f}')} | "
                f"{_fmt_delta(d and d['tokens_pct'], '{:.1f}%')} | "
                f"{_fmt_ci(s['cost_usd'], '{:.3f}')} | "
                f"{_fmt_ci(ti) if ti['n'] else '—'} (n={ti['n']}) | "
                f"{_fmt_ci(tn) if tn['n'] else '—'} (n={tn['n']}) |"
            )

    routing = [
        (t["task_id"], a, s)
        for t in analysis["tasks"]
        for a, s in t["arms"].items()
        if a in NATURAL_ARMS
    ]
    if routing:
        out += [
            "\n## Routing: does the agent pick the skill up unprompted?\n",
            "| task | arm | natural invocation rate [95% CI] |",
            "|---|---|---:|",
        ]
        for task_id, arm, s in routing:
            rate = s["invocation_rate"]
            if rate is None:
                out.append(f"| {task_id} | {arm} | n/a |")
            else:
                out.append(
                    f"| {task_id} | {arm} | {_fmt_ci(rate, pct=True)} "
                    f"({s['invoked']}/{s['invoked_n']}) |"
                )

    benefit = [
        (t["task_id"], t["arms"]["treatment-forced"])
        for t in analysis["tasks"]
        if "treatment-forced" in t["arms"] and t["arms"]["treatment-forced"]["delta_vs_baseline"]
    ]
    if benefit:
        out += [
            "\n## Benefit: treatment-forced vs baseline\n",
            "| task | Δ pass rate [95% CI] | Δ tokens [95% CI] | Δ % | forced invocation rate |",
            "|---|---:|---:|---:|---:|",
        ]
        for task_id, s in benefit:
            d = s["delta_vs_baseline"]
            rate = s["invocation_rate"]
            out.append(
                f"| {task_id} | {_fmt_delta(d['pass_rate'], pct=True)} | "
                f"{_fmt_delta(d['tokens'], '{:.0f}')} | "
                f"{_fmt_delta(d['tokens_pct'], '{:.1f}%')} | "
                f"{'n/a' if rate is None else _fmt_ci(rate, pct=True)} |"
            )
    return "\n".join(out) + "\n"


def summarize(records: Iterable[dict[str, Any] | BenchmarkRecord]) -> str:
    return render_markdown(analyze(records))


def write_summary(records: Iterable[dict[str, Any] | BenchmarkRecord], path: Path) -> str:
    """Write summary.md at path and summary.json next to it; return the markdown."""
    analysis = analyze(records)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "summary.json").write_text(json.dumps(analysis, indent=2) + "\n")
    summary = render_markdown(analysis)
    path.write_text(summary, encoding="utf-8")
    return summary

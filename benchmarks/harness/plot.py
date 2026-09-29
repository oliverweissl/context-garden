"""Render the benchmark figure from a results dir's records.jsonl.

Grouped bar chart: one bar
group per metric, one bar per component, height = treatment vs baseline
delta in % of the baseline mean, whiskers = 95% bootstrap CI of that delta
(10k resamples, fixed seed). With several treatment arms (treatment-natural,
treatment-forced) there is one panel per arm, stacked.

matplotlib is an optional dependency (`pip install -e '.[bench]'`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import ARMS

METRICS = {
    "input_tokens": "Input tokens",
    "output_tokens": "Output tokens",
    "cache_read_tokens": "Cache read tokens",
    "total_tokens": "Total tokens",
    "tool_calls": "Tool calls",
    "runtime": "Runtime",
    "cost_usd": "Cost $",
}
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 42
MUTED = "#6b6a63"


class MatplotlibMissing(RuntimeError):
    pass


def _import_pyplot():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on env
        raise MatplotlibMissing(
            "matplotlib is required for `scripts/benchmark plot`; install the bench "
            "extra: pip install -e '.[bench]'  (or: pip install matplotlib)"
        ) from exc
    return plt


def bootstrap_deltas(
    records: list[dict[str, Any]], arm: str, components: list[str]
) -> dict[str, dict[str, tuple[float, float, float] | None]]:
    """{component: {metric: (delta %, err_low, err_high)}} for `arm` vs baseline.

    delta = 100 * (mean(treatment) / mean(baseline) - 1); the CI resamples both
    arms independently. None when either arm has no records or baseline mean is 0.
    """
    import numpy as np

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    out: dict[str, dict[str, tuple[float, float, float] | None]] = {}
    for component in components:
        rows = [r for r in records if r.get("component") == component]
        out[component] = {}
        for metric in METRICS:
            b = np.array([r.get(metric) or 0 for r in rows if r["condition"] == "baseline"])
            t = np.array([r.get(metric) or 0 for r in rows if r["condition"] == arm])
            if not len(b) or not len(t) or b.mean() == 0:
                out[component][metric] = None
                continue
            bs = rng.choice(b, (BOOTSTRAP_SAMPLES, len(b))).mean(1)
            ts = rng.choice(t, (BOOTSTRAP_SAMPLES, len(t))).mean(1)
            with np.errstate(divide="ignore", invalid="ignore"):
                boot = 100 * (ts / bs - 1)
            boot = boot[np.isfinite(boot)]
            delta = float(100 * (t.mean() / b.mean() - 1))
            lo, hi = np.percentile(boot, [2.5, 97.5]) if len(boot) else (delta, delta)
            out[component][metric] = (delta, max(0.0, delta - lo), max(0.0, hi - delta))
    return out


def plot_records(
    records: list[dict[str, Any]], out_path: Path, version: str | None = None
) -> Path:
    import numpy as np

    plt = _import_pyplot()
    present = {r["condition"] for r in records}
    arms = [a for a in ARMS if a in present and a != "baseline"]
    arms += sorted(present - set(ARMS) - {"baseline"})  # legacy "treatment"
    if "baseline" not in present or not arms:
        raise ValueError("need baseline records and at least one treatment arm to plot")
    components = sorted({r["component"] for r in records})

    x, w = np.arange(len(METRICS)), 0.75 / len(components)
    fig, axes = plt.subplots(len(arms), 1, figsize=(12, 6 * len(arms)), squeeze=False)
    for ax, arm in zip(axes[:, 0], arms):
        deltas = bootstrap_deltas(records, arm, components)
        for i, component in enumerate(components):
            vals = [deltas[component][m] for m in METRICS]
            heights = [v[0] if v else 0.0 for v in vals]
            err = [[v[1] if v else 0.0 for v in vals], [v[2] if v else 0.0 for v in vals]]
            ax.bar(
                x + (i - (len(components) - 1) / 2) * w, heights, w, yerr=err,
                capsize=4, label=component,
            )  # fmt: skip
        ax.axhline(0, color="black", lw=1)
        ax.set(
            xticks=x, xticklabels=list(METRICS.values()),
            ylabel="Treatment vs baseline delta (%)",
        )  # fmt: skip
        if len(arms) > 1:
            ax.set_title(f"{arm} vs baseline", loc="left", fontsize=11)
        ax.legend(frameon=False)
        ax.grid(axis="y", alpha=0.2)

    n = sorted(
        {sum(1 for r in records if r["component"] == c and r["condition"] == a)
         for c in components for a in ["baseline", *arms]} - {0}
    )  # fmt: skip
    footer = f"n per component x arm: {', '.join(map(str, n))}; 95% bootstrap CI"
    if version:
        footer = f"context-garden v{version} · {footer}"
    fig.text(0.99, 0.0, footer, ha="right", va="top", fontsize=8, color=MUTED)
    out_path = Path(out_path)
    fig.savefig(out_path, bbox_inches="tight", dpi=100)
    plt.close(fig)
    return out_path


def plot_results_dir(
    results_dir: Path, out_name: str = "benchmark.png", version: str | None = None
) -> Path:
    results_dir = Path(results_dir)
    lines = (results_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return plot_records(records, results_dir / out_name, version)

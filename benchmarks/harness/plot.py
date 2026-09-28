"""Render the benchmark figure from summary.json (see analyze.py).

Two panels, tasks on the y axis, one dot + 95% CI whisker per arm:
  left   verified pass rate (Wilson CI)
  right  total-token delta vs baseline, % of baseline mean (Welch CI)

matplotlib is an optional dependency (`pip install -e '.[bench]'`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import ARMS

# Fixed categorical order (arm -> colour never changes between figures).
ARM_COLORS = {
    "baseline": "#2a78d6",
    "treatment-natural": "#eb6834",
    "treatment-forced": "#1baf7a",
    "treatment": "#eda100",  # legacy records
}
MUTED = "#6b6a63"
GRID = "#e4e3dc"


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


def _err(t: dict[str, Any] | None, scale: float = 1.0) -> tuple[float, float, float] | None:
    if not t or t.get("value") is None:
        return None
    v = t["value"] * scale
    if t.get("lo") is None:
        return v, 0.0, 0.0
    # clamp: floating-point noise can put a bound a hair past the point estimate
    return v, max(0.0, v - t["lo"] * scale), max(0.0, t["hi"] * scale - v)


def plot_summary(analysis: dict[str, Any], out_path: Path) -> Path:
    plt = _import_pyplot()
    tasks = analysis["tasks"]
    if not tasks:
        raise ValueError("summary has no tasks to plot")
    present = {a for t in tasks for a in t["arms"]}
    arms = [a for a in ARMS if a in present] + sorted(present - set(ARMS))
    task_ids = [t["task_id"] for t in tasks]

    height = max(2.6, 0.75 * len(tasks) * max(1, len(arms)) / 2 + 1.4)
    fig, (ax_pass, ax_tok) = plt.subplots(1, 2, figsize=(11, height), sharey=True)
    step = 0.8 / max(1, len(arms))
    offsets = {a: (i - (len(arms) - 1) / 2) * step for i, a in enumerate(arms)}

    for yi, task in enumerate(tasks):
        for arm in arms:
            s = task["arms"].get(arm)
            if s is None:
                continue
            y = yi + offsets[arm]
            color = ARM_COLORS.get(arm, MUTED)
            p = _err(s["pass_rate"], 100)
            if p:
                ax_pass.errorbar(
                    p[0], y, xerr=[[p[1]], [p[2]]], fmt="o", ms=6, color=color,
                    ecolor=color, elinewidth=2, capsize=0,
                    label=None if arm in _labels(ax_pass) else arm,
                )  # fmt: skip
            d = s.get("delta_vs_baseline")
            t = _err(d and d["tokens_pct"])
            if t:
                ax_tok.errorbar(
                    t[0], y, xerr=[[t[1]], [t[2]]], fmt="o", ms=6, color=color,
                    ecolor=color, elinewidth=2, capsize=0,
                )  # fmt: skip

    ax_pass.set_xlim(-2, 102)
    ax_pass.set_xlabel("verified pass rate, % (95% Wilson CI)")
    ax_pass.set_title("Pass rate", loc="left", fontsize=11)
    ax_tok.axvline(0, color=MUTED, lw=1)
    ax_tok.set_xlabel("total tokens vs baseline, % (95% Welch CI; <0 = fewer)")
    ax_tok.set_title("Token delta vs baseline", loc="left", fontsize=11)
    ax_pass.set_yticks(range(len(task_ids)), task_ids)
    ax_pass.invert_yaxis()
    for ax in (ax_pass, ax_tok):
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(GRID)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=MUTED)

    handles, labels = ax_pass.get_legend_handles_labels()
    order = sorted(range(len(labels)), key=lambda i: arms.index(labels[i]))
    fig.legend(
        [handles[i] for i in order], [labels[i] for i in order],
        loc="upper center", ncol=len(arms), frameon=False, bbox_to_anchor=(0.5, 1.0),
    )  # fmt: skip
    n_by_arm = sorted({s["n"] for t in tasks for s in t["arms"].values()})
    fig.text(
        0.99, 0.005, f"n per task x arm: {', '.join(map(str, n_by_arm))}",
        ha="right", va="bottom", fontsize=8, color=MUTED,
    )  # fmt: skip
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    out_path = Path(out_path)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _labels(ax) -> set[str]:
    return set(ax.get_legend_handles_labels()[1])


def plot_results_dir(results_dir: Path, out_name: str = "benchmark.png") -> Path:
    results_dir = Path(results_dir)
    summary_json = results_dir / "summary.json"
    return plot_summary(
        json.loads(summary_json.read_text(encoding="utf-8")), results_dir / out_name
    )

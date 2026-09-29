"""Offline tests of records.jsonl persistence, summary.md generation,
and the ClaudeCodeRunner transcript helpers (no real agent invoked)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness import runners  # noqa: E402
from harness.analyze import (  # noqa: E402
    analyze,
    load_records,
    mean_ci,
    newcombe_diff_ci,
    render_markdown,
    summarize,
    t_crit,
    welch_diff_ci,
    wilson,
    write_records,
    write_summary,
)
from harness.types import BenchmarkRecord  # noqa: E402


def _record(condition, verified, output_tokens, skill_invoked=None):
    return BenchmarkRecord(
        task_id="pruner-file-tokenusage-bug",
        component="pruner",
        level="file",
        condition=condition,
        model="m",
        success=True,
        verification_success=verified,
        verification_notes="",
        output_tokens=output_tokens,
        skill_invoked=skill_invoked or [],
    )


def test_records_round_trip_and_summary(tmp_path):
    records = [
        _record("baseline", True, 100),
        _record("baseline", False, 300),
        _record("treatment-natural", True, 100, ["pruner"]),
        _record("treatment-natural", True, 100, ["context-garden:pruner"]),
    ]
    path = write_records(records, tmp_path / "run" / "records.jsonl")
    rows = load_records(path)
    assert len(rows) == 4
    assert rows[2]["skill_invoked"] == ["pruner"]
    assert rows[0]["total_tokens"] == 100

    summary = write_summary(rows, tmp_path / "run" / "summary.md")
    assert (tmp_path / "run" / "summary.md").read_text() == summary
    data = json.loads((tmp_path / "run" / "summary.json").read_text())
    arms = data["tasks"][0]["arms"]
    assert list(arms) == ["baseline", "treatment-natural"]
    base, treat = arms["baseline"], arms["treatment-natural"]
    assert base["n"] == 2 and base["passes"] == 1
    assert base["tokens"]["value"] == 200
    # t(1) = 12.706, sd = 141.42 -> half-width 1270.6
    assert base["tokens"]["lo"] == pytest.approx(200 - 1270.6, abs=1)
    assert treat["invoked"] == 2 and treat["invocation_rate"]["value"] == 1.0
    assert treat["delta_vs_baseline"]["tokens"]["value"] == -100
    assert treat["delta_vs_baseline"]["tokens_pct"]["value"] == pytest.approx(-50.0)
    assert treat["delta_vs_baseline"]["pass_rate"]["value"] == pytest.approx(0.5)
    assert (
        "| pruner-file-tokenusage-bug | baseline | 2 | 1/2 | 50% [9%, 91%] | — | 0/2 |" in summary
    )
    assert "## Routing" in summary and "100% [34%, 100%] (2/2)" in summary


def test_summary_marks_invocation_unknown_for_old_records():
    row = _record("baseline", True, 10).to_dict()
    del row["skill_invoked"]
    assert "| n/a |" in summarize([row])


def test_legacy_treatment_condition_is_reported_as_routing():
    rows = [_record("baseline", True, 10), _record("treatment", True, 5, ["pruner"])]
    summary = summarize(rows)
    assert "| pruner-file-tokenusage-bug | treatment | 1 | 1/1 |" in summary
    assert "## Routing" in summary


def test_benefit_section_compares_forced_to_baseline():
    rows = [_record("baseline", i < 2, 100 + i) for i in range(10)] + [
        _record("treatment-forced", i < 8, 50 + i, ["pruner"]) for i in range(10)
    ]
    analysis = analyze(rows)
    d = analysis["tasks"][0]["arms"]["treatment-forced"]["delta_vs_baseline"]
    assert d["pass_rate"]["value"] == pytest.approx(0.6)
    assert 0 < d["pass_rate"]["lo"] < 0.6 < d["pass_rate"]["hi"] <= 1
    assert d["tokens"]["value"] == pytest.approx(-50)
    assert d["tokens"]["lo"] < -50 < d["tokens"]["hi"] < 0
    assert "## Benefit: treatment-forced vs baseline" in render_markdown(analysis)


def test_stats_helpers_match_reference_values():
    p, lo, hi = wilson(5, 10)
    assert (p, round(lo, 4), round(hi, 4)) == (0.5, 0.2366, 0.7634)
    assert t_crit(9) == 2.262
    assert t_crit(1000) == pytest.approx(1.962, abs=1e-3)
    m, lo, hi = mean_ci([1, 2, 3, 4, 5])
    assert m == 3 and hi - m == pytest.approx(2.776 * 1.5811 / 5**0.5, rel=1e-3)
    # Newcombe (1998) example: 56/70 vs 48/80 -> 0.2, CI [0.0524, 0.3339]
    d, lo, hi = newcombe_diff_ci(56, 70, 48, 80)
    assert (round(d, 4), round(lo, 4), round(hi, 4)) == (0.2, 0.0524, 0.3339)
    d, lo, hi = welch_diff_ci([1, 2, 3], [1, 2, 3])
    assert d == 0 and lo < 0 < hi


def test_transcript_path_maps_every_non_alnum_char(tmp_path, monkeypatch):
    monkeypatch.setattr(runners.Path, "home", staticmethod(lambda: tmp_path))
    workdir = tmp_path / "context_garden-bench.x"
    workdir.mkdir()
    encoded = "".join(c if c.isalnum() else "-" for c in str(workdir.resolve()))
    transcript = tmp_path / ".claude" / "projects" / encoded / "sid.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    assert runners._local_transcript_path(workdir, "sid") == transcript


def test_skill_tool_invocation_counts_tokens_and_names(tmp_path):
    lines = [
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "pruner"}}
                ],
                "usage": {"cache_creation_input_tokens": 5},
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        },
        {
            "type": "assistant",
            "message": {"id": "m2", "content": [], "usage": {"cache_creation_input_tokens": 700}},
        },
    ]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(line) for line in lines))
    tokens, invoked = runners._skill_tokens_from_transcript(transcript, tmp_path)
    assert tokens == 700
    assert invoked == ["pruner"]


def test_denied_skill_call_is_not_counted_as_invoked(tmp_path):
    lines = [
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "mycelium"}},  # fmt: skip
                ],
                "usage": {"cache_creation_input_tokens": 5},
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                     "content": "Permission for this tool use was denied."}
                ]
            },
        },  # fmt: skip
        {
            "type": "assistant",
            "message": {"id": "m2", "content": [], "usage": {"cache_creation_input_tokens": 700}},
        },
    ]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(line) for line in lines))
    assert runners._skill_tokens_from_transcript(transcript, tmp_path) == (0, [])


def test_runner_allows_bash_and_skill_by_default():
    assert {"Bash", "Skill"} <= set(runners.ClaudeCodeRunner().allowed_tools)

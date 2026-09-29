# Benchmarks

Compares an agent with vs. without a Skill, on tasks built from this
repository (real injected bug, real oversized Skill, niche correctness
scenario).

## Layout

```text
harness/      fixture discovery/materialization, agent runners, verification, analysis
fixtures/     one dir per task (task.yaml + optional bug.patch / overlay/)
results/      generated output (gitignored)
```

## Fixtures

| task_id | component | level | tests |
|---|---|---|---|
| `pruner-file-tokenusage-bug` | pruner | file | locates the one relevant file instead of exploring broadly |
| `compost-noisy-failure-cluster` | compost | repo | clusters 17 failures down to 2 root causes instead of reading raw output |
| `seedbank-recurring-facts` | seedbank | repo | pre-populated `AGENTS.md` avoids re-discovering scattered facts |
| `weeder-bloated-skill-audit`* | weeder | file | shrinks an oversized `SKILL.md` without losing a constraint |
| `mycelium-overlapping-investigation` | mycelium | repo | overlapping 4-part question: fewer, better-briefed agents; the one-line lookup stays in the main thread |
| `mycelium-notes-reuse` | mycelium | repo | two sessions on one subsystem: notes from session 1 cut session 2's rediscovery |

\* `run_by_default: false` — skipped by a plain `scripts/benchmark run`
(needs `--task`/`--component` to run); see
[why](../docs/benchmarking.md#why-weeder-is-opt-in).

Commands, cost, methodology, and record fields: `docs/benchmarking.md`.

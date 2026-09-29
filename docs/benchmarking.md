# Benchmarking

The benchmark compares an agent working without a Skill with the same agent when that Skill is available, on tasks built from this repository. The task index is in [`benchmarks/README.md`](../benchmarks/README.md#fixtures).

## Commands

```bash
scripts/benchmark list
scripts/benchmark run --dry-run
scripts/benchmark run [--task ID ...] [--component NAME ...] \
    [--arms baseline treatment-natural treatment-forced] [--runs 10] [--seed 0] \
    [--model M] [--max-budget-usd 1.0]
scripts/benchmark run --resume benchmarks/results/<UTC timestamp>
scripts/benchmark analyze benchmarks/results/<UTC timestamp>
scripts/benchmark plot benchmarks/results/<UTC timestamp>   # needs pip install -e '.[bench]'
```

`run` starts real headless `claude -p` sessions: sessions = tasks × arms × runs, each capped by `--max-budget-usd`. See `--dry-run` for the schedule and cost bound before running. Results go to `benchmarks/results/<UTC timestamp>/`.

## What is compared?

| Arm | What the agent receives | Prompt |
| --- | --- | --- |
| `baseline` | No installed Skills | Original task |
| `treatment-natural` | The task's Skill(s), installed in `.claude/skills/` | Original task |
| `treatment-forced` | The same Skill(s) | Original task plus an explicit instruction to use the Skill(s), from `skill_relevance` in `task.yaml` |

- **Routing:** Does the agent choose the Skill on its own? Look at `skill_invoked` for `treatment-natural`.
- **Benefit:** Does the Skill help when the agent is told to use it? Compare `treatment-forced` with `baseline`. The forced arm's invocation rate also checks whether the agent followed the instruction.

Exception: `seedbank-recurring-facts` (`treatment_mode: preseeded`) gives its treatment arms a prepared `AGENTS.md` instead of a Skill, and the forced prompt tells the agent to read it (`forced_prompt_prefix`). An invocation rate of 0 is expected; compare pass rates and tokens instead.

Older records may say `condition: treatment`; the analysis treats that as `treatment-natural`.

## How trials run

Use at least the default `--runs 10`; with substantially fewer, confidence intervals are usually too wide to distinguish effects. Trials are shuffled and interleaved with a fixed `--seed`; `schedule.json` stores the full order.

`--resume` skips task/arm/repetition combinations already in `records.jsonl`. A last line cut off by a crash is ignored and that trial reruns.

### Isolation between arms

Working copies are identical except for the treatment payload. They exclude the Skill's repository copy, fixture answers and harness tests, previous results, `AGENTS.md`, `CLAUDE.md`, `.claude/`, the plugin manifest, and hooks (`EXCLUDED_PATHS` in `harness/fixtures.py`), so repository plugin hooks run in no arm, including `baseline`.

Every arm uses `--setting-sources project` and `--strict-mcp-config`, without `--plugin-dir`, so user settings, plugins, hooks, and MCP servers do not load. **One remaining leak is `~/.claude/skills/`: Claude Code can still load Skills from there.** Keep it empty on the benchmark machine, or use a clean `HOME` with only the `claude` login credentials.

Every arm passes `--allowedTools Bash`. With `--permission-mode acceptEdits` alone, a headless run has no approval surface and auto-denies every Bash call (pytest and skill CLIs included), which silently invalidated v0.2.0. Trials where the model produced no tokens are recorded with `infra_error` and excluded from analysis; check that count before trusting a run.

`ClaudeCodeRunner` adds a unique nonce to each call's system prompt, so no run benefits from another run's warm prompt cache; caching within a run still works.

## Reading the results

`summary.md` names its statistical methods in its header; all intervals are 95%. Plot error bars are bootstrap intervals (10,000 resamples, seed 42).

1. **Start with verified pass rate.** `verification_success` comes from `harness/verify.py` inspecting the working copy, not from the agent's claim. A token saving counts only if the pass rate holds up.
2. **Then tokens and cost**, including the split by whether the agent actually invoked the task's Skill, so a few unusually cheap or expensive invoked runs do not vanish into the average.
3. **Use the routing and benefit tables** to separate Skill discovery from the effect of using it.

### Tasks with a single correct answer

Some tasks require exactly one `KEY: value` line: `ROOT_CAUSE: <function_name>` for compost, or `VERDICT: OK` / `VERDICT: REGRESSION` for trellis. The `answer_key` verifier fails if the key is missing, repeated, or paired with a value that does not equal `expected` or fully match `pattern`.

## Why trellis and weeder are opt-in

Both set `run_by_default: false` (select with `--component` or `--task`) because a single-task comparison shows their cost but not their benefit: trellis spends tokens to catch numerical regressions, and weeder's savings arrive in future uses of the optimized Skill. A positive token delta for them is expected.

## Record fields

Fields are defined in `harness/types.py::BenchmarkRecord`. Non-obvious ones:

- `repository_tokens` uses `cache_creation_input_tokens` (new content counted once), **not** `cache_read_input_tokens`, which recounts the same content on each tool turn. `cache_read_tokens` and `cost_usd` are diagnostics and are not added to `total_tokens`.
- `skill_invoked` records Skills used through the Skill tool or by reading `SKILL.md`.
- A non-empty `infra_error` means the model never ran. Older records without the field are detected by zero input and output tokens.

## Publishing results for a version

Each run stores the context-garden version from `.claude-plugin/plugin.json` in `schedule.json`. After a full run for a release:

```bash
scripts/benchmark plot benchmarks/results/<run-dir> --publish
```

This writes `benchmark.png`, copies it and `summary.md` to `docs/benchmarks/v<version>/`, and updates the figure between the `benchmark-figure` markers in `README.md`. Add a row for the release to [`docs/benchmarks/README.md`](benchmarks/README.md), and mark invalid runs there (as v0.2.0 is). For runs from before version 0.2, supply `--version` because their schedules do not record it.

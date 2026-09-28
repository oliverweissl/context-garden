# Benchmarking

The benchmark compares an agent working without a Skill with the same agent when that Skill is available. Each task comes from this repository: for example, a real bug inserted into a file, an oversized Skill, or a niche correctness check. See `benchmarks/README.md` for the fixture format, task index, and commands.

## Quick start

```bash
scripts/benchmark list
scripts/benchmark run --dry-run
scripts/benchmark run
scripts/benchmark analyze benchmarks/results/<UTC timestamp>
```

The dry run shows the trial schedule and estimated cost without starting any sessions. A normal run uses three default tasks, three arms, and ten repetitions: **90 sessions**. Past runs cost about **$10–15** in total, though the ceiling is 90 × `--max-budget-usd` (default $1 per session).

Results appear in `benchmarks/results/<UTC timestamp>/`: `schedule.json`, `records.jsonl`, `summary.md`, and `summary.json`.

## What is compared?

Each task can run in three **arms**. `--arms` selects arms; the default is all three.

| Arm | What the agent receives | Prompt |
| --- | --- | --- |
| `baseline` | No installed Skills | Original task |
| `treatment-natural` | The task's Skill(s), installed in `.claude/skills/` | Original task |
| `treatment-forced` | The same Skill(s) | Original task plus an explicit instruction to use the Skill(s), from `skill_relevance` in `task.yaml` |

The arms answer two separate questions:

- **Routing:** Does the agent choose the Skill on its own? Look at `skill_invoked` for `treatment-natural`.
- **Benefit:** Does the Skill help when the agent is told to use it? Compare `treatment-forced` with `baseline`. The forced arm's invocation rate also checks whether the agent followed the instruction.

One exception is `seedbank-recurring-facts` (`treatment_mode: preseeded`). Its treatment arms receive a prepared `AGENTS.md` instead of a Skill. The forced prompt tells the agent to read that file (`forced_prompt_prefix` in `task.yaml`). No Skill is invoked, so an invocation rate of 0 is expected. Compare its pass rates and tokens instead.

Older records may say `condition: treatment`. The analysis keeps that name but treats it like `treatment-natural`.

## How trials run

The default is **10 repetitions per task and arm** (`--runs 10`). With substantially fewer than ten, confidence intervals are usually too wide to distinguish effects.

The runner shuffles and interleaves trials using a fixed seed (`--seed 0` by default). Each repetition contains one trial for every selected task and arm. This spreads changes in latency, rate limits, or model behavior across arms. `schedule.json` stores the seed, selected tasks and arms, repetition count, and full order. Each record also stores `rep`, `seed`, and `order_index`.

Completed trials are appended to `records.jsonl`. If a run stops, resume it with:

```bash
scripts/benchmark run --resume benchmarks/results/<UTC timestamp>
```

The runner skips recorded task/arm/repetition combinations. If the last line was cut off when the process stopped, it ignores that line and reruns the trial.

### Isolation between arms

Working copies are identical except for the treatment payload. A treatment arm gets either its Skill in `.claude/skills/` (the default `skill_available` mode) or a prepared `AGENTS.md` overlay (`preseeded` mode).

The working copy excludes the Skill's repository copy, fixture answers and harness tests, previous results, `AGENTS.md`, `CLAUDE.md`, `.claude/`, the plugin manifest, and hooks. The exclusions are defined in `EXCLUDED_PATHS` in `harness/fixtures.py`. This prevents repository plugin hooks from running in any arm, including `baseline`.

Every arm uses `--setting-sources project` and `--strict-mcp-config`, without `--plugin-dir`. User settings, enabled plugins and their hooks, and user MCP servers therefore do not load. **One remaining leak is `~/.claude/skills/`: Claude Code can still load Skills from there.** Keep it empty on the benchmark machine, or use a clean `HOME` with only the `claude` login credentials.

`ClaudeCodeRunner` adds a unique nonce to each call's system prompt. This prevents one run from benefiting from another run's warm prompt cache; caching within a run still works.

## Reading the results

Run `scripts/benchmark analyze <results-dir>` to create a readable `summary.md` and machine-readable `summary.json`. All reported confidence intervals are **95%**.

1. **Start with verified pass rate.** `verification_success` comes from `harness/verify.py` inspecting the working copy, not from the agent's claim. The report gives pass rates by task and arm, Wilson score intervals, and changes from baseline with Newcombe hybrid-score intervals.
2. **Then look at tokens and cost.** The report gives mean `total_tokens` and `cost_usd`, t-based intervals, and absolute and percentage changes from baseline with Welch t intervals. It also separates token use by whether the agent actually invoked the task's Skill, so a small group of unusually cheap or expensive invoked runs does not disappear into the average.
3. **Use the routing and benefit tables** to distinguish Skill discovery from the effect of using it.

A token saving is useful only if the pass rate holds up. That is why pass rate appears first.

### Tasks with a single correct answer

Some tasks require exactly one `KEY: value` line: `ROOT_CAUSE: <function_name>` for compost, or `VERDICT: OK` / `VERDICT: REGRESSION` for trellis. The `answer_key` verifier fails if the key is missing, repeated, or paired with a value that does not equal `expected` or fully match `pattern`. See `harness/verify.py` for the other verification types.

## Commands and cost

```bash
scripts/benchmark list
scripts/benchmark run --dry-run
scripts/benchmark run [--task ID ...] [--component NAME ...] \
    [--arms baseline treatment-natural treatment-forced] [--runs 10] [--seed 0] \
    [--model M] [--max-budget-usd 1.0]
scripts/benchmark run --resume benchmarks/results/<UTC timestamp>
scripts/benchmark analyze benchmarks/results/<UTC timestamp>
```

**Sessions = tasks × arms × runs.** Each session is a headless `claude -p` run capped by `--max-budget-usd` (default $1). The default 3 × 3 × 10 run has 90 sessions; past sessions cost roughly $0.10–0.15 each. Adding trellis and weeder makes 150 sessions. Check `--dry-run` for the estimate and maximum before running.

## Plotting results

```bash
pip install -e '.[bench]'  # optional matplotlib dependency
scripts/benchmark plot benchmarks/results/<UTC timestamp>
```

The plot command regenerates the summary from `records.jsonl` and writes `benchmark.png`. It shows treatment-versus-baseline percentage changes for input, output, and cache-read tokens; total tokens; tool calls; runtime; and cost. Bars are grouped by metric, with one bar per component. Error bars are 95% bootstrap intervals (10,000 resamples, seed 42). Multiple treatment arms get separate panels. If matplotlib is missing, the command reports which extra to install.

## Why trellis and weeder are opt-in

`trellis` and `weeder` set `run_by_default: false` in their `task.yaml` fixtures. A plain `scripts/benchmark run` skips both. Select one with `--component trellis`, `--component weeder`, or `--task <id>`.

| Component | Why it can use more tokens now | What to measure / when to use it |
| --- | --- | --- |
| **trellis** | It writes a spec script, checks numerical behavior, and reports findings. | Whether it catches real regressions. Use after changes to numerical code such as solvers, discretization, optimizers, or Monte Carlo methods. See `skills/trellis/SKILL.md`. |
| **weeder** | Optimizing a Skill costs tokens in the current task. Savings happen in future uses of that Skill, outside this single-task comparison. | Use while authoring or reviewing a Skill whose `SKILL.md` is oversized, repeats rules, or has a routing description too generic to trigger reliably. |

`summary.md` reports both like any other component. A positive token delta for these tasks is expected; interpret it in light of their purpose.

## Record fields

Each line in `records.jsonl` contains:

| Group | Fields |
| --- | --- |
| Identity and schedule | `task_id`, `component`, `level` (`file` or `repo`), `condition`, `model`, `rep`, `seed`, `order_index` |
| Outcome | `success`, `verification_success` |
| Tokens | `input_tokens`, `repository_tokens`, `tool_result_tokens`, `skill_tokens`, `output_tokens`, `total_tokens` |
| Activity | `tool_calls`, `repository_reads`, `retries`, `runtime` |
| Diagnostics | `cache_read_tokens`, `cost_usd`, `skill_invoked` |

`condition` is `baseline`, `treatment-natural`, or `treatment-forced` (older records may use `treatment`). `skill_invoked` records Skills the agent used through the Skill tool or by reading `SKILL.md`.

`total_tokens` is the sum of the five token fields before it; see `harness/types.py::BenchmarkRecord`. `repository_tokens` uses `cache_creation_input_tokens`: new content counted once. It does **not** use `cache_read_input_tokens`, which can count the same content again on each tool turn. `cache_read_tokens` and `cost_usd` help check the metric against actual rereads and dollars; neither is added to `total_tokens`.

## Publishing results for a version

Each run stores the context-garden version from `.claude-plugin/plugin.json` in `schedule.json`. After a full run for a release:

```bash
scripts/benchmark plot benchmarks/results/<run-dir> --publish
```

This writes `benchmark.png`, copies it and `summary.md` to `docs/benchmarks/v<version>/`, and updates the figure between the `benchmark-figure` markers in `README.md`. Older versions remain in `docs/benchmarks/`. Add a row for the release to `docs/benchmarks/README.md`. For runs from before version 0.2, supply `--version` because their schedules do not record it.
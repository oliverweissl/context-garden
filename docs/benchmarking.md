# Benchmarking

Compares baseline agent vs. baseline + Skill, on tasks built from this
repository (a real bug injected into a real file, a real Skill made
oversized, a niche correctness scenario) — see `benchmarks/README.md` for
the fixture format, index, and run commands.

## Metric

`summary.md` (`harness/analyze.py`) reports exactly one thing per
component: mean baseline tokens vs. mean treatment tokens, as a % gain.
Nothing else — no success rate, no verified rate, no tool calls, no
cost. Those still exist per-row in the raw `records.jsonl`
(`verification_success` is computed independently of the agent's own
claim — see `harness/verify.py`) for anyone who wants to check
correctness wasn't traded away for the token win; `summary.md` itself
doesn't surface it.

## Baseline vs. treatment

Identical working copies except for one thing: `treatment` has the
relevant `skills/<name>/` installed under `.claude/skills/`
(`treatment_mode: skill_available`, default), or — for `seedbank`, whose
value is the committed `AGENTS.md` a prior session would produce, not a
CLI invoked mid-task — a pre-populated `AGENTS.md` overlay
(`treatment_mode: preseeded`).

`ClaudeCodeRunner` appends a unique nonce to the system prompt on every
call, so no run can get a cheaper ride off another run's warmed
prompt-cache — every run pays full price for what it actually reads.
Within-run caching across one run's own tool-call turns is untouched.

## trellis and weeder: not part of a plain run

Not every Skill here is trying to reduce tokens for the task it runs in
— trellis and weeder aren't, see below. `summary.md` no longer
special-cases anything: if you run one, it gets the same % gain row as
everything else, and that number will legitimately be negative. Instead
they set `run_by_default: false` in their fixture's `task.yaml`, so a
plain `scripts/benchmark run` (no `--task`/`--component`) skips them
entirely and spends no API budget on either. Run one deliberately with
`scripts/benchmark run --component trellis` (or `--task <id>`) — and
read the negative % gain as expected, not as a bug.

- **trellis** is a correctness gate, not a compression tool. It spends
  extra tokens (write a spec script, run checks, produce a report) to
  buy numerical/scientific confidence a passing test suite can't provide
  — see `skills/trellis/SKILL.md`'s central thesis. Costing more tokens
  than baseline is the expected outcome, every time; the thing worth
  measuring is whether it catches a real regression (verified rate),
  not whether it's cheaper. Use it after any change to numerical code
  (solvers, discretization schemes, optimizers, Monte Carlo methods) —
  skip it for anything that isn't.
- **weeder** shrinks *other* Skills' always-loaded token cost. Its
  payoff lands in every future invocation of the Skill it just
  optimized, not in the task where it's invoked — running it costs
  tokens now for a saving that shows up elsewhere later, structurally
  invisible to a single-task before/after comparison. Use it when
  authoring or reviewing a Skill (a `SKILL.md` feels oversized, has a
  rule stated twice, or a routing description too generic to trigger
  reliably) — not as part of a normal task.

## Record schema

```yaml
task_id:
component:
level:            # file | repo
condition:        # baseline | treatment
model:

success:
verification_success:

input_tokens:
repository_tokens:
tool_result_tokens:
skill_tokens:
output_tokens:
total_tokens:

tool_calls:
repository_reads:
retries:
runtime:

cache_read_tokens:   # diagnostic only, not in total_tokens
cost_usd:            # diagnostic only, not in total_tokens
```

`total_tokens` is the sum of the five token fields above it
(`harness/types.py::BenchmarkRecord`). `repository_tokens` comes from
`cache_creation_input_tokens` (content newly read into context, counted
once) — not `cache_read_input_tokens`, which is repeated re-reads of
already-counted content across a run's own tool-call turns and would
double/triple/N-count the same material as a function of turn count
alone. `cache_read_tokens`/`cost_usd` are reported for sanity-checking
the token metric against real re-read volume and $ cost, never summed
into `total_tokens`.

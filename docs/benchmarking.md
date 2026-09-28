# Benchmarking

Compares a baseline agent vs. the same agent with a Skill available (and explicitly told to use it), on tasks built from this
repository (a real bug injected into a real file, a real Skill made
oversized, a niche correctness scenario) — see `benchmarks/README.md` for
the fixture format, index, and run commands.

## Methodology

### Arms

Every task runs under up to three arms (`--arms`, default all three):

| arm | working copy | prompt |
|---|---|---|
| `baseline` | no skills installed | original |
| `treatment-natural` | task's skill(s) installed under `.claude/skills/` | original |
| `treatment-forced` | same as `treatment-natural` | original, prefixed with an explicit instruction to use the task's skill(s) (`skill_relevance` in `task.yaml`) |

The two treatment arms answer different questions and are reported
separately:

- **Routing** — does the agent pick the skill up unprompted? The
  natural invocation rate of `treatment-natural` (from the recorded
  `skill_invoked`).
- **Benefit** — when the skill *is* used, does it help? `treatment-forced`
  vs `baseline`. The forced arm's own invocation rate is shown as a
  compliance check.

For `seedbank-recurring-facts` (`treatment_mode: preseeded`) the treatment
arms get a pre-populated `AGENTS.md` instead of an installed skill, and
the forced prefix (`forced_prompt_prefix` in its `task.yaml`) tells the
agent to read it. No Skill is invoked there, so its invocation rate is
always 0 and not meaningful — read its pass-rate/token deltas only.

Older records with `condition: treatment` are treated as
`treatment-natural`-equivalent (shown under their own name).

### Repetitions and order

- `--runs N` repetitions per (task, arm); **default 10**. Below ~10 the
  CIs are too wide to distinguish most effects.
- Execution order is **randomised and interleaved** with a fixed
  `--seed` (default 0): repetition-major blocks, each containing every
  (task, arm) pair once in shuffled order. Arms alternate throughout the
  run, so drift over time (latency, rate limits, model-side changes)
  hits every arm about equally.
- The seed, arms, runs, tasks and the full order are written to
  `schedule.json`; each record also carries `rep`, `seed` and
  `order_index`.
- **Resumable.** Each record is appended to `records.jsonl` as soon as
  its trial finishes. `scripts/benchmark run --resume <results-dir>`
  re-reads `schedule.json` and skips every (task, arm, rep) already
  recorded (a torn last line from a killed process is ignored and the
  trial re-run).

### Metrics

`scripts/benchmark analyze` writes `summary.md` (readable) and
`summary.json` (what `plot` reads). All intervals are 95% CIs.

- **Primary: verified pass rate** per task × arm — `verification_success`
  from `harness/verify.py`, computed from the working copy, never the
  agent's own claim. Wilson score CI; delta vs baseline with a Newcombe
  hybrid-score CI.
- **Secondary: tokens and cost** — mean `total_tokens` and `cost_usd`
  with t-based CIs; delta vs baseline (absolute and % of the baseline
  mean) with a Welch t CI. Tokens are also split by whether the task's
  skill was actually invoked in that run, so a cheap/expensive
  minority of invoked runs isn't averaged away.
- **Routing** and **Benefit** tables as described above.

Don't read a token saving as a win unless the pass rate held: the
primary table comes first for that reason.

### Verification: structured single answers

Tasks whose answer is a single fact ask for exactly one `KEY: value`
line — `ROOT_CAUSE: <function_name>` (compost), `VERDICT: OK` /
`VERDICT: REGRESSION` (trellis). The `answer_key` verify type fails if
the key is missing, appears more than once (hedging with several
candidates), or the value doesn't equal `expected` / fully match
`pattern`. See `harness/verify.py` for all verify types.

### Isolation

Identical working copies except for the treatment payload: `treatment`
arms have the relevant `skills/<name>/` installed under
`.claude/skills/` (`treatment_mode: skill_available`, default), or a
pre-populated `AGENTS.md` overlay (`treatment_mode: preseeded`). The
working copy never contains the skill under test's `skills/<name>/`,
fixture answers (`benchmarks/fixtures/`, the harness tests), prior
results, `AGENTS.md`/`CLAUDE.md`, `.claude/`, or the plugin manifest
and hooks (`.claude-plugin/`, `hooks/` — e.g. seedbank's PostToolUse
hooks), so no arm, least of all `baseline`, can trigger a plugin hook
from the repository (`EXCLUDED_PATHS` in `harness/fixtures.py`).

Every arm runs with `--setting-sources project` and
`--strict-mcp-config` and without `--plugin-dir`, so user-level
settings, enabled plugins (their skills and hooks) and user MCP servers
don't load. That does not stop Claude Code from loading Skills in the
user-level `~/.claude/skills/` directory — a Skill installed there leaks
into `baseline`. Keep `~/.claude/skills/` empty on the benchmark
machine, or run with a clean `HOME` (a throwaway directory holding only
the `claude` login credentials).

`ClaudeCodeRunner` appends a unique nonce to the system prompt on every
call, so no run gets a cheaper ride off another run's warmed prompt
cache. Within-run caching across one run's own tool-call turns is
untouched.

## Running

```bash
scripts/benchmark list
scripts/benchmark run --dry-run                      # print schedule + cost bound, run nothing
scripts/benchmark run [--task ID ...] [--component NAME ...] \
    [--arms baseline treatment-natural treatment-forced] [--runs 10] [--seed 0] \
    [--model M] [--max-budget-usd 1.0]
scripts/benchmark run --resume benchmarks/results/<UTC timestamp>   # after an interruption
scripts/benchmark analyze benchmarks/results/<UTC timestamp>
```

Results land in `benchmarks/results/<UTC timestamp>/`
(`schedule.json`, `records.jsonl`, `summary.md`, `summary.json`).

### Cost

**Sessions = tasks × arms × runs**, each a real headless `claude -p`
session capped by `--max-budget-usd` (default $1.00). A default run
(3 default tasks × 3 arms × 10 runs) is 90 sessions; past runs cost
roughly $0.10–0.15 per session, so expect ~$10–15, with a hard ceiling
of 90 × `--max-budget-usd`. `--dry-run` prints both numbers before you
spend anything. Adding trellis and weeder makes it 150 sessions.

## Plotting

```bash
pip install -e '.[bench]'        # matplotlib (optional dependency)
scripts/benchmark plot benchmarks/results/<UTC timestamp>
```

Regenerates `summary.json` from `records.jsonl` and writes
`benchmark.png` into the results directory: verified pass rate per task
× arm with Wilson CIs (left) and the total-token delta vs baseline, % of
baseline mean, with Welch CIs (right). Without matplotlib the command
exits with an error naming the extra to install.

## trellis and weeder: not part of a plain run

Not every Skill here is trying to reduce tokens for the task it runs in
— trellis and weeder aren't, see below. `summary.md` doesn't
special-case anything: if you run one, it gets the same rows as
everything else, and its token delta will legitimately be positive
(more tokens). Instead
they set `run_by_default: false` in their fixture's `task.yaml`, so a
plain `scripts/benchmark run` (no `--task`/`--component`) skips them
entirely and spends no API budget on either. Run one deliberately with
`scripts/benchmark run --component trellis` (or `--task <id>`) — and
read the token increase as expected, not as a bug.

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
condition:        # baseline | treatment-natural | treatment-forced (legacy: treatment)
model:
rep:              # repetition index within (task, arm)
seed:             # schedule seed
order_index:      # position in the randomised schedule

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
skill_invoked:       # skills the agent invoked (Skill tool / SKILL.md read)
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

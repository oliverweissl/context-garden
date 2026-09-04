# Benchmarking

The benchmark framework exists now; the benchmark corpus does not yet. This document describes the intended shape so future component work has somewhere to land.

## Goal

Context Garden's core claim is that its components improve context efficiency without sacrificing correctness. That claim needs to be measured, not asserted — see principle 7 ("measure before optimizing") in `docs/architecture.md`.

## Primary metric

```text
verified successful results
───────────────────────────
total tokens consumed
```

Raw token reduction is not itself a win; a component that saves tokens but reduces verified success rate is a regression.

## Comparisons

The primary comparison for each component:

```text
baseline agent
vs.
baseline + Context Garden component
```

Once multiple components exist, also measure:

```text
baseline
vs.
full Context Gardening stack
```

## Record schema

```yaml
task_id:
component:
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
```

The token fields correspond to `context_garden.core.tokens.TokenUsage`.

## Layout

- `benchmarks/fixtures/` — task inputs: sample repositories, prompts, and expected outcomes, organized per component.
- `benchmarks/results/` — generated run output (gitignored; not source).
- `scripts/benchmark` — the entry point that will eventually discover fixtures, run them against baseline and component-enabled agents, and emit records in the schema above.

## Status

`scripts/benchmark` currently accepts an empty suite and exits successfully — there's nothing to benchmark until the first Skill exists. Fixtures and comparisons will be added alongside each Skill's implementation, per `docs/skill-development.md`.

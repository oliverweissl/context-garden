# Benchmarks

Evaluation infrastructure for Context Garden components. The harness exists now; the full benchmark corpus does not yet.

## Layout

- `fixtures/` — task inputs (sample repositories, prompts, expected outcomes) used to drive benchmark runs. Empty until the first component has something to benchmark against.
- `results/` — generated benchmark output. Not source; ignored by git except for a placeholder.

## Record schema

Each benchmark run should eventually produce records shaped like:

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

See `TokenUsage` in `src/context_garden/core/tokens.py` for the token-accounting fields this schema is built on.

## Primary comparison

```text
baseline agent
vs.
baseline + Context Garden component
```

Eventually, once multiple components exist:

```text
baseline
vs.
full Context Gardening stack
```

## Running

`scripts/benchmark` is the entry point. It currently accepts an empty benchmark suite — there is nothing to run until fixtures and a first Skill exist.

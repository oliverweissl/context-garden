# 🌱 Context Garden

Context Garden trims the weeds from your agent's context so the important information—the crops—can thrive.

## Why Context Garden?

Agent context windows fill up with waste long before they fill up with useful information:

- repeated repository reads, because agents rediscover the same facts every session
- oversized persistent instructions (`AGENTS.md`, system prompts) that accumulate and never get pruned
- enormous raw tool outputs (compiler logs, test runs, CI, profilers) dumped into context wholesale
- irrelevant source loaded because the agent can't tell what's relevant ahead of time
- low-quality evidence accepted at face value instead of independently verified

Context Garden is a family of Agent Skills that address these problems directly, backed by shared tooling and conventions.

## Components

**Context Cache** — Learns which repository knowledge agents repeatedly rediscover and promotes stable, high-value information into compact persistent context, while preventing stale or low-value information from accumulating in files like `AGENTS.md`.

**Context Slice** — Selects the smallest useful portion of a repository for the task at hand, giving agents the relevant source, tests, configuration, and dependencies without loading or searching the rest of the codebase.

**Tracefold** — Compresses large compiler, test, CI, HPC, profiler, and scientific-computing outputs into compact, evidence-preserving representations, drastically reducing tool-result context while keeping the full raw output retrievable.

**Numerical Sentinel** — An independent correctness layer for numerical and scientific-computing tasks, verifying claims using evidence such as residuals, convergence, invariants, precision checks, reproducibility, and statistical validation, rather than relying only on code execution or unit tests.

**Skill Debloater** — Analyzes and optimizes Agent Skills themselves, reducing unnecessary instructions, duplicated rules, oversized references, and poor routing while preserving or improving functional quality.

## Status

Early development. Repository infrastructure is being established. Individual Skills will be released incrementally as their specifications are finalized.

## Repository Structure

```text
src/         shared implementation source (the context_garden runtime package)
skills/      distributable Agent Skills — each independently installable
tests/       repository tests; never shipped with a Skill
benchmarks/  evaluation infrastructure and datasets
```

Every directory under `skills/<skill-name>/` is meant to eventually be installable on its own, without access to the rest of this repository. See [`docs/architecture.md`](docs/architecture.md) for the full rationale.

## Philosophy

Maximize useful information per context token. Do not optimize token count at the expense of correctness — compression must never destroy access to the original evidence.

## Installation

No Skills have been released yet. Installation instructions will be added alongside the first published Skill.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

See [`LICENSE`](LICENSE).

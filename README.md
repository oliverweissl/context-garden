# 🌱 Context Garden

**Trim the weeds from your agent's context so the important information—the crops—can thrive.**

Context Garden is a family of self-contained Agent Skills that help coding and scientific agents use less context, waste fewer tokens, avoid repeated work, and produce better-verified results — offline and deterministically, no LLM calls inside the tooling itself.

## Why use it?

If your agents keep rereading the same files, carrying a bloated `AGENTS.md`, dumping huge compiler/test logs into context, loading far more source than they need, or declaring numerical work correct too early — Context Garden is built to fix each of those.

> **The goal: more useful information per context token.** Less noise, less rediscovery, better evidence — never compress away the evidence itself.

## The Garden

| Skill | Status | Does |
|---|---|---|
| 🌰 [`seedbank`](skills/seedbank) | ✅ implemented | Tracks which repo facts get rediscovered repeatedly and promotes only the worth-it ones into a small, always-loaded `AGENTS.md`; stale facts auto-invalidate. |
| ✂️ [`pruner`](skills/pruner) | ✅ implemented | Given a concrete task/error/failing test, returns the exact `file:start-end` ranges worth reading (Python + C/C++), budget-enforced, instead of grep-exploring. |
| ♻️ [`compost`](skills/compost) | ✅ implemented | Compacts huge compiler/test/CI/HPC/profiler output into clustered, evidence-preserving summaries; the full raw output stays retrievable on demand. |
| 🌿 [`trellis`](skills/trellis) | ✅ implemented | Independent correctness gate for numerical/scientific code — checks convergence order, residuals, conditioning, and reproducibility instead of trusting "tests passed". |
| 🌾 [`weeder`](skills/weeder) | ✅ implemented | Audits a Skill's always-loaded token cost, mechanically moves background/examples into references, and validates the rewrite still routes and functions correctly before you accept it. Optionally supports opt-in `llm_assist` levels (`none`/`slight`/`lot`) that structure its one genuinely judgment-requiring step (description/duplicate-rule rewriting) as a request/answer file pair for the invoking agent instead of freeform editing — see [`references/llm-assist.md`](skills/weeder/references/llm-assist.md); this never adds an LLM call inside the tooling itself. |

Each Skill is self-contained: its own `SKILL.md`, CLI (`bin/<name>`), `references/`, and a `tests/smoke_test.sh` it validates itself with (see each Skill's `validate.md`).

## Try one

```bash
skills/compost/bin/compost --help
bash skills/compost/tests/smoke_test.sh   # runs offline against bundled fixtures
```

Same pattern for `seedbank` (`bin/seedbank`), `pruner` (`bin/pruner`), `trellis` (`bin/trellis`, needs `numpy`), and `weeder` (`bin/weeder`). To use one outside this repo, copy its `skills/<name>/` directory into your project's Skills location, or point a Git-based Skill installer at that subdirectory — nothing in it reaches back into this monorepo.

## Status

✅ All five Skills are implemented and self-validating (table above). Shared runtime infrastructure (`src/context_garden`) and repository tooling are in place.

## Repository Structure

```text
src/         shared Context Garden runtime and implementation
skills/      independently installable Agent Skills
tests/       repository-level tests: src/context_garden + cross-skill structural checks
benchmarks/  evaluation infrastructure and datasets
```

Every `skills/<skill-name>/` directory works independently, without requiring the rest of this repository. See [`docs/architecture.md`](docs/architecture.md) for the architecture and packaging model, [`docs/skill-development.md`](docs/skill-development.md) for the Skill layout convention, and [`docs/benchmarking.md`](docs/benchmarking.md) for the evaluation schema.

## Philosophy

**Maximize useful information per context token.** Context Garden does not optimize token count blindly — compression must never destroy access to evidence, and token savings should never come at the expense of correctness. The long-term metric it's built to optimize:

```text
verified successful results
───────────────────────────
total context consumed
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

See [`LICENSE`](LICENSE).

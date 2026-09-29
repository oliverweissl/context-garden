# 🌱 Context Garden

Self-contained Agent Skills that cut an agent's context usage — deterministic, offline, no LLM calls inside the tooling.

| Skill | Does |
|---|---|
| 🌰 [`seedbank`](skills/seedbank) | Promotes repeatedly-rediscovered repo facts into a small, always-loaded `AGENTS.md`. |
| ✂️ [`pruner`](skills/pruner) | Returns exact `file:start-end` ranges worth reading (Python + C/C++) instead of grep-exploring. |
| ♻️ [`compost`](skills/compost) | Compacts huge compiler/test/CI/HPC output into clustered summaries. |
| 🌿 [`trellis`](skills/trellis) | Correctness gate for numerical code (convergence order, residuals, conditioning). |
| 🌾 [`weeder`](skills/weeder) | Shrinks a Skill's always-loaded token cost without losing instructions. |

<!-- benchmark-figure:begin -->
No valid published benchmark yet: the v0.2.0 run is invalid (headless Bash was
auto-denied, so no skill CLI ever ran); see [`docs/benchmarks/`](docs/benchmarks).
<!-- benchmark-figure:end -->

## How to use
```bash
cp -r skills/{skill} ~/.claude/skills/{skill}   # or project's .claude/skills/
```

CLI help and offline smoke tests:
```bash
skills/{skill}/bin/{skill} --help
bash skills/{skill}/tests/smoke_test.sh   # offline, bundled fixtures
```

## Docs

[`docs/architecture.md`](docs/architecture.md) (layout, self-containment rule, design principles) ·
[`docs/skill-development.md`](docs/skill-development.md) (adding a Skill) ·
[`docs/benchmarking.md`](docs/benchmarking.md) (running and plotting benchmarks) ·
[`examples/`](examples) (measured before/after).
Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md). License: [`LICENSE`](LICENSE).

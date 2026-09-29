# Architecture

## Repository layout

| Directory | Purpose |
| --- | --- |
| `skills/` | Distributable Agent Skills; each Skill lives in its own self-contained folder. |
| `tests/` | Repository tests, including checks for Skill structure and self-containment. |
| `benchmarks/` | Evaluation harness and fixtures that measure whether a Skill helps. |
| `docs/` | Documentation, including this file, `skill-development.md`, and `benchmarking.md`. |
| `scripts/` | Repository tools: `validate-skills`, `build-skills`, and `benchmark`. |

## Skill self-containment (critical rule)

**A Skill must work using only files inside its own directory.** An installer may copy just `skills/<name>/`, so a reference such as `../../benchmarks/...` can break after installation.

If a Skill needs shared code, bundle it during the build with `scripts/build-skills` or include it in the Skill's own `scripts/` directory. Run `scripts/validate-skills` after changing any `skills/<name>/`; it checks this rule.

## Design principles

These principles guide the Skills in `skills/`:

1. **Treat context as scarce.** Instructions and tool results compete for the agent's attention. A Skill should help the agent read less while still doing the job well.
2. **Summarize without losing evidence.** A compost cluster or pruner slice should preserve a route back to the exact source line. The agent should not have to repeat the work that produced the summary.
3. **Load detail only when needed.** Keep `SKILL.md` short because it is read on every use. Put predictable logic in `scripts/` and occasional detail in `references/`.
4. **Use code for deterministic tasks.** Parsing, scoring, and budget enforcement belong in tools when there is one correct answer. Do not rely on prompt instructions to reproduce that answer every time.
5. **Optimize for verified results per token.** Saving tokens is not an improvement if answers get worse. `docs/benchmarking.md` explains how results are checked independently of the agent's claims.
6. **Prove the benefit with benchmarks.** A Skill earns its place by outperforming a baseline agent on a real task, not by sounding convincing in its specification. See `docs/benchmarking.md`.

## The `.context-garden/` runtime directory

Skills can use `.context-garden/` for project-local state. Only `config.yaml` is committed; generated files are gitignored.

```yaml
llm_assist:
  level: none  # none | slight | lot
```

`llm_assist` changes how a step that already requires the agent's judgment is organized. For example, weeder can exchange request and answer files for a description rewrite instead of editing freehand. It **does not** cause the tooling to call an LLM. See `skills/weeder/references/llm-assist.md` for an example.
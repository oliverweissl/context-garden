# Architecture

## Repository layout

`skills/` (one self-contained dir per Skill), `tests/`, `benchmarks/` (harness + fixtures), `docs/`, and
`scripts/`: repo tools `validate-skills`, `validate-routing`, `build-skills`, `calibrate-tokens`, `benchmark`.

## Skill self-containment (critical rule)

**A Skill must work using only files inside its own directory.** An installer may copy just `skills/<name>/`, so a reference such as `../../benchmarks/...` can break after installation.

If a Skill needs shared code, bundle it during the build with `scripts/build-skills` or include it in the Skill's own `scripts/` directory. Run `scripts/validate-skills` after changing any `skills/<name>/`; it checks this rule.

## Design principles

1. **Verified results per token, proven by benchmark.** Saving tokens is no win if answers get worse; a Skill earns its place by beating a baseline agent on a real task ([`benchmarking.md`](benchmarking.md)).
2. **Summarize without losing evidence.** A compost cluster or pruner slice should preserve a route back to the exact source line. The agent should not have to repeat the work that produced the summary.
3. **Load detail only when needed.** Keep `SKILL.md` short because it is read on every use. Put predictable logic in `scripts/` and occasional detail in `references/`.
4. **Use code for deterministic tasks.** Parsing, scoring, and budget enforcement belong in tools when there is one correct answer, not in prompt instructions.

## The `.context-garden/` runtime directory

Skills can use `.context-garden/` for project-local state. Everything in it except `config.yaml` is gitignored (this repo ships none).

```yaml
llm_assist:
  level: none  # none | slight | lot
```

`llm_assist` changes how a step that already requires the agent's judgment is organized. For example, weeder can exchange request and answer files for a description rewrite instead of editing freehand. It **does not** cause the tooling to call an LLM. See `skills/weeder/references/llm-assist.md` for an example.

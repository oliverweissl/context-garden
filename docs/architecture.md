# Architecture

## Layout

```text
skills/      distributable Agent Skills, one self-contained dir each
tests/       repo-level tests (Skill structure/self-containment checks)
benchmarks/  evaluation harness + fixtures that measure each Skill's win
docs/        this file, skill-development.md, benchmarking.md
scripts/     repo tooling: validate-skills, build-skills, benchmark
```

## Skill self-containment (critical rule)

A Skill must never require a path outside its own directory (e.g.
`../../benchmarks/...`) — installers may copy only `skills/<name>/`, so
anything it depends on has to travel inside that one folder. Shared code
a Skill needs must be bundled at build time (`scripts/build-skills`) or
vendored into the Skill's own `scripts/`. Checked by
`scripts/validate-skills` — run it after touching any `skills/<name>/`.

## Principles

Everything in `skills/` exists in service of these; when a design choice
in a Skill looks unusual, it's almost always tracing back to one of them.

- **Context is scarce.** Every persistent instruction and every tool
  result an agent reads competes for the same limited attention — a
  Skill's job is to make an agent read less, not just do more.
- **Compress, never destroy evidence.** A summary (a compost cluster, a
  pruner slice) must keep the original source retrievable — an agent
  should be able to go from "here's the gist" to "here's the exact line"
  without redoing the work that produced the gist.
- **Progressive disclosure.** Keep `SKILL.md` small — it's paid for on
  every use. Deterministic logic lives in `scripts/`; depth an agent only
  sometimes needs lives in `references/`, loaded on demand.
- **Deterministic tooling over prompt instructions.** Anything with one
  correct answer (parsing, scoring, budget enforcement) should be code,
  not an instruction hoping the agent gets it right each time.
- **Optimize `verified successful results / total tokens consumed`**, not
  raw token count — a Skill that saves tokens by giving worse answers is
  not a win. See `docs/benchmarking.md` for how `verified` is checked
  independently of the agent's own claim.
- **Measure, don't assert the win.** A Skill earns its place in this repo
  by beating a baseline agent on a real benchmark task, not by reading
  well in a spec. See `docs/benchmarking.md`.

## `.context-garden/` runtime directory

Project-local state for Skills that use it. Only `config.yaml` is
committed; everything else is generated/gitignored.

```yaml
llm_assist:
  level: none   # none | slight | lot
```

`llm_assist` structures a workflow step that already needs an agent's
judgment (e.g. weeder's description rewrite) as a request/answer file
instead of freehand editing — it never means the tooling calls an LLM
itself. See `skills/weeder/references/llm-assist.md` for the reference
implementation.

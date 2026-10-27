# 🌱 Context Garden

Self-contained Agent Skills that cut an agent's context usage — deterministic, offline, no LLM calls inside the tooling.

**[See the showcase →](https://oliverweissl.github.io/context-garden/)**

| Skill | Does |
|---|---|
| 🌰 [`seedbank`](skills/seedbank) | Promotes repeatedly-rediscovered repo facts into a small, always-loaded `AGENTS.md`. |
| ✂️ [`pruner`](skills/pruner) | Returns exact `file:start-end` ranges worth reading (Python + C/C++) instead of grep-exploring. |
| ♻️ [`compost`](skills/compost) | Compacts huge compiler/test/CI/HPC output into clustered summaries. |
| 🍄 [`mycelium`](skills/mycelium) | Delegation gate for subagents (bounded brief, no duplicate investigations) plus shared finding notes so agents don't rediscover the same code. |
| 🏡 [`greenhouse`](skills/greenhouse) | Shared memory across the repos of one platform: per-repo cards (provides, depends on, key facts) in a base folder beside the repos, so an agent knows its neighbours without reading them. |
| 🌾 [`weeder`](skills/weeder) | Shrinks a Skill's always-loaded token cost without losing instructions. |

<!-- benchmark-figure:begin -->
Benchmark results for **v0.2.0** on the small fixtures in this repo, run with Claude Code ([summary](docs/benchmarks/v0.2.0/summary.md); older versions in [`docs/benchmarks/`](docs/benchmarks)):

![Benchmark results for context-garden v0.2.0](docs/benchmarks/v0.2.0/benchmark.png)
<!-- benchmark-figure:end -->

## Install

As a plugin (all six skills, their hooks and agents, see [below](#hooks-and-agents)):
```text
/plugin marketplace add oliverweissl/context-garden
/plugin install context-garden@context-garden
/context-garden:setup    # skills start off: pick the ones for your use case
```
Or copy single skills (the hooks then need the settings snippet in the skill's `references/`):
```bash
cp -r skills/{skill} ~/.claude/skills/{skill}   # or the project's .claude/skills/
```

## After installing: turn skills on

**Every skill starts `off`.** Nothing runs, not even the hooks, until you pick which skills
you want. Until you do, each new session shows a reminder. With the plugin, run:
```text
/context-garden:setup                    # asks how you work and where to apply it
/context-garden:setup platform           # or name a preset directly
/context-garden:setup swarm --scope user # ... for every repo, not just this one
```
Or just ask Claude, e.g. "turn on compost and pruner". Changes apply from the next prompt.

### Which skills for which use case

| You work... | Preset | Turned on |
|---|---|---|
| in one repo, everyday coding and debugging | `solo` | seedbank, pruner, compost |
| on a big project split over several repos that call each other (services, shared packages, APIs) | `platform` | + greenhouse |
| with many subagents per task (parallel search, investigate, edit) | `swarm` | + mycelium |
| with many agents across a multi-repo platform | `platform-swarm` | + greenhouse, mycelium |
| mostly with long builds, test suites, CI or HPC/SLURM logs | `logs` | compost only |

Skills a preset does not turn on are set to `manual`: they never run on their own but still
answer when you call them (e.g. `/context-garden:weeder` when you want to slim a Skill).
`all` turns everything on; `off` keeps everything off and hides the reminder.

Rule of thumb: **greenhouse** pays off only when several repos interact, **mycelium** only when
Claude delegates to many agents; **compost**, **pruner** and **seedbank** help in almost any repo;
**weeder** is for people who write Skills.

Scopes: `local` (default; this repo, only you, `.claude/settings.local.json`), `project` (this
repo, shared with the team via `.claude/settings.json`), `user` (every repo, `~/.claude/settings.json`).

## Modes: on, manual or off

Each skill has one of three modes.

| Mode | Claude uses it on its own | You can call it | Its hooks run |
|---|---|---|---|
| `on` | yes, when relevant | yes | yes |
| `manual` | no | yes, with an instruction | no |
| `off` | no | no | no |

Set or show a mode (or ask Claude to, e.g. "turn compost on"):
```bash
skills/compost/bin/compost mode on         # this repo, only you: .claude/settings.local.json
skills/compost/bin/compost mode manual --scope user  # every repo (or --scope project to share)
skills/compost/bin/compost mode            # show the mode and where it comes from
```
With the plugin, `bin/` sits in the plugin's install directory, so use `/context-garden:setup`,
ask Claude, or edit the setting directly. The mode is stored as Claude Code's own `skillOverrides` setting, in
`.claude/settings.local.json` (this repo, only you), `.claude/settings.json` (shared) or
`~/.claude/settings.json` (all repos), most specific first:
```json
{"skillOverrides": {"compost": "on", "pruner": "user-invocable-only"}}
```
`"on"`, `"user-invocable-only"` (= `manual`) and `"off"` are the values; a skill with no entry is
`off`. For copied skills the `/skills` menu edits the same entries (Space cycles; "user-only" is
`manual`), but Claude Code itself treats a missing entry as on, so the skill's own check does the
switching off there too.
`CONTEXT_GARDEN_MODE_<SKILL>=on|manual|off` overrides everything for one shell.

Call a skill directly with an instruction after its name; this works in `on` and `manual`
(an `off` skill declines and tells you how to turn it on):
```text
/compost summarize the failing pytest run                 # copied skill
/context-garden:compost summarize the failing pytest run  # plugin
```

With the plugin, Claude Code ignores `skillOverrides` for plugin skills, so each skill checks
its mode itself when it loads: in `manual` it proceeds only if you asked for it, in `off` it
stops. Its one-line description is still listed to Claude (about 60-100 tokens per skill). To
drop that too, copy the skill instead of using the plugin, or disable the whole plugin with
`/plugin disable context-garden`.

## Optional: the desktop app

Everything above works without it. If you want one window for a whole platform, `app/`
holds a small desktop app (Windows, macOS, Linux; Tauri + xterm.js) that:

- starts a **garden** from repos, local folders, GitLab groups (with subgroups) or GitHub orgs,
  typed into one field and told apart automatically; clones nested by group, or flat;
- recommends and applies the skills per repo (same `skillOverrides` as `/context-garden:setup`);
- shows garden stats (repos, uncommitted changes, always-loaded agent-file tokens, seedbank facts,
  greenhouse cards, Claude Code tokens of the last 30 days);
- runs Claude Code, Codex, Gemini CLI or a plain shell in arrangeable terminal panes, optionally
  under named **accounts** (separate `CLAUDE_CONFIG_DIR` / `CODEX_HOME`, each with its own icon).

It talks to GitHub/GitLab only through your logged-in `gh` / `glab` CLIs and never touches agent
credentials. Build and run: see [`app/README.md`](app/README.md).

## Hooks and agents

Skills teach Claude *how* to do a job; the deterministic parts run as hooks, and jobs worth
delegating ship as agents. Every hook is a no-op unless its skill is `on`; the one exception is
a session-start reminder to run `/context-garden:setup`, shown only while no skill has a mode set.

| Skill | Hook | Agent |
|---|---|---|
| compost | before Bash: a raw build/test command is denied with the compost command to run instead (`hook` in `.compost/config.json`: `redirect`, `ask`, `rewrite`, `off`) | |
| pruner | on prompt submit: a pasted traceback/compiler error gets a slice as context | preloaded into `scout` |
| seedbank | after Read/Grep/Glob: record observations; at stop: once per session, ask to curate this session's candidates | `gardener` curates and promotes |
| mycelium | before Agent: brief gate; when an agent stops: record its `Notes:` block | `scout` read-only search |
| greenhouse | session start: platform overview, or ask where the base is; session end: card sync if the base opted in | `envoy` reads another repo's checkout |
| weeder | | `weeder` runs the whole slimming workflow |

## CLI help and smoke tests

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

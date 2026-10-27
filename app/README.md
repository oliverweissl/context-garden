# Context Garden desktop app

Optional. The skills work without it; this is one window for a whole platform:
gardens of repos, the skills they use, and agent terminals side by side.

Stack: [Tauri 2](https://tauri.app) (Rust) + plain JavaScript + [xterm.js](https://xtermjs.org).
Terminals are real pseudo-terminals (`portable-pty`, ConPTY on Windows).

## Build and run

Needs Rust (`rustup`), Node 18+, and the [Tauri prerequisites](https://tauri.app/start/prerequisites/)
for your OS (WebView2 on Windows, `webkit2gtk` on Linux).

```bash
cd app
npm install
npm run tauri dev      # run from source
npm run tauri build    # installer for this OS in src-tauri/target/release/bundle/
```

`npm run dev` alone serves the UI in a normal browser with fake data (`src/mock.js`),
handy for UI work; it is never part of a build.

## What it does

- **Gardens.** One input field takes repo URLs, local folders, GitLab groups (with
  subgroups) and GitHub orgs. Single-segment paths are groups/orgs, `.git` / `git@` URLs
  and GitHub `owner/repo` are repos; ambiguous GitLab paths are looked up with `glab`.
  Groups are listed with your logged-in `gh` / `glab`, then cloned 4 at a time with
  `GIT_TERMINAL_PROMPT=0`. Nested layout (default) keeps `group/subgroup/repo`; flat puts
  every repo in the garden folder and prefixes clashing names. Existing clones are reused,
  never overwritten.
- **Skills.** A preset is recommended from the repo count (1: `solo`, 2-9: `platform`,
  10+: `platform-swarm`) and written to every repo's `.claude/settings.local.json`
  `skillOverrides`, exactly like `hooks/garden.py preset ... --scope local`. Unparseable
  settings files are left alone. With greenhouse on, `<garden>/.greenhouse` becomes the base.
- **Dashboard.** Repos and groups, repos with uncommitted changes, always-loaded agent
  file tokens (AGENTS.md + CLAUDE.md, ~4 chars/token), seedbank facts, greenhouse cards,
  and Claude Code token use of the last 30 days read from local transcripts
  (`~/.claude/projects/` and every Claude account folder).
- **Terminals.** Claude Code, Codex, Gemini CLI or a plain shell, in the garden root or a
  repo; grid / columns / rows, drag a pane header onto another to swap, maximize, close.
  Terminals keep running while you look at another garden. Agents start through your
  login shell so PATH and npm shims match a normal terminal. Panes get `CONTEXT_GARDEN`
  and, if the garden has one, `GREENHOUSE_BASE`.
- **Accounts.** Several subscriptions per agent: each account is its own config folder
  (`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for Codex) with a name, icon and
  color shown on its terminals. Log in once inside a terminal of that account; the CLI
  stores the credential itself. The app never reads, copies or sends credentials.
- **Garden art.** Each garden card grows a small, unique garden hashed from its id:
  16 plots, 4 plant types, 64 bloom colors, 16 stem colors, widths, sizes and heights;
  one plant per repo (3 to 16).

App state lives in the OS app-data folder (`gardens.json`, `profiles.json`, `profiles/`).
Removing a garden or account only forgets it; nothing on disk is deleted.

## Tests

```bash
cd app/src-tauri && cargo test && cargo clippy --all-targets
```

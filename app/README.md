# ContextGarden desktop app

Optional. The skills work without it; this is one window for a whole platform:
gardens of repos, the skills they use, and agent terminals side by side.

Stack: [Tauri 2](https://tauri.app) (Rust) + plain JavaScript + [xterm.js](https://xtermjs.org).
Terminals are real pseudo-terminals (`portable-pty`, ConPTY on Windows).

## Install

Download the installer for your OS from
[Releases](https://github.com/oliverweissl/context-garden/releases): `.dmg` (macOS),
`.msi` / `.exe` (Windows), `.deb` / `.rpm` / `.AppImage` (Linux). The builds are not
code-signed yet, so the first start needs one extra step:

- **macOS:** right-click ContextGarden in Applications, then Open (or run
  `xattr -dr com.apple.quarantine /Applications/ContextGarden.app`).
- **Windows:** SmartScreen, then "More info", then "Run anyway".

The app needs `git` and Claude Code and/or Codex on your PATH. For GitHub orgs it also
needs `gh` (`brew install gh` / `winget install GitHub.cli`, then `gh auth login`); for
GitLab groups `glab` (`brew install glab` / `winget install glab.glab`, then
`glab auth login` with a personal access token that has the `api` and `write_repository`
scopes; add `--hostname gitlab.example.com` for self-hosted GitLab). Single repo URLs and
local folders need neither. When a newer release is out, a "Version … available" button
appears next to Settings.

## Use it

1. **Settings → Accounts** (optional): add one per subscription. A sign-in terminal opens
   and the skills are installed into that account. Each account (and each agent's default
   login) lists its plugins with their version; Update installs a newer one (Claude Code
   and Codex).
2. **Start a garden:** name it, paste repos, folders, GitLab groups or GitHub orgs (one per
   line), Find repositories, Plant garden. Failed clones can be retried.
3. **Settings → Skills:** take the recommended preset or set each skill on / manual / off.
   External plugins (any Claude Code plugin marketplace) can be added for this garden only.
   **Repositories** lists the garden's repos and adds more; **Resync groups** lists the
   garden's groups again, clones repos new in them and offers to remove repos that left
   (from the garden only, never from disk).
4. **Terminals:** pick an agent or account and a folder from the tree, Open. Panes tile the
   area and can be resumed later (↺ Recent).

## Build from source

Needs Rust, Node 18+, and the [Tauri prerequisites](https://tauri.app/start/prerequisites/)
for your OS (Xcode Command Line Tools on macOS, WebView2 on Windows, `webkit2gtk` on Linux):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Windows: winget install Rustlang.Rustup
cd app
npm install
npm run tauri build    # installer for this OS in src-tauri/target/release/bundle/
```

Install the bundle and use that. `npm run tauri dev` is for working on the app itself;
some features work less well in the dev build.

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
  and agent token use of the last 30 days per account (Claude Code and Codex), read
  from local transcripts and shown as a donut.
- **Terminals.** Claude Code, Codex, Gemini CLI or a plain shell, in the garden root, a
  group folder or a repo. Drag a pane header onto another pane's edge to split it there,
  drag the gaps to resize; grid / columns / rows rearrange all, maximize, close. Shift+Enter
  is a line break. An agent's header shows where it works now (`→ repo`, from its
  transcript); its dot turns yellow when its account got newer plugins than it started
  with (resume it to load them) and red when it reports a usage or credit limit.
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

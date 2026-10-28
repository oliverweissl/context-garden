# greenhouse: operational details

## Finding the base

First hit wins:

1. `--base <dir>` or `$GREENHOUSE_BASE`.
2. `<git root>/.greenhouse`: a directory holding `greenhouse.json` is the
   base itself (e.g. a git submodule); a *file* holds a path to the base,
   relative to the git root (written by `init`/`link`, commit it).
3. `.greenhouse/` or `greenhouse/` holding `greenhouse.json` in any
   ancestor of the git root. This covers the common workspace layout:
   ```
   ~/work/platform/
     .greenhouse/      <- base
     api/  web/  shared-lib/
   ```

`bin/greenhouse where` prints the base and how it was found. `init` creates
`<repo parent>/.greenhouse/` (or `--at <dir>`) and writes the pointer file
when ancestor search would not find it. Nothing else ever creates a base.

When there is none, `mode on` settles it right away: on a terminal it asks
(new base beside the repo / another path / skip); run by Claude, it prints
the three choices for Claude to put to the user. While greenhouse is on
and no base exists, the session-start hook prints the same choices, so the
question comes up once per session until it is answered (or greenhouse is
turned off for the repo).

## Base layout

```
greenhouse.json   marker: {"version": 1, "name": ...}
repos/<slug>.json one card per repo
notes.json        platform notes
INDEX.md          generated overview for humans (repos, links, notes)
```

The slug is the basename of the `origin` remote (stable across clones),
else the directory name. A card has two parts: *harvested* fields, replaced
on every `sync`, and *curated* fields (`summary`, `provides`, `consumes`,
`owners`), which `sync` only adds to and never clears. Remove a whole card
with `forget <slug>`; edit curated fields by hand in the JSON if needed.

What is harvested: README first paragraph, top languages, top-level
layout, packages and dependencies from `pyproject.toml`, `package.json`,
`go.mod`, `Cargo.toml`, `requirements.txt` (root and one level down),
entrypoints (project scripts, npm scripts), the seedbank block of
`AGENTS.md` (else bullets of a hand-written `AGENTS.md`/`CLAUDE.md`),
`.seedbank/warm/` topic names and `.mycelium/notes.json` findings. Only
tracked files and these stores are read; nothing leaves the machine except
into the base folder. Credentials in the remote URL are stripped.

Links: repo A -> B when A depends on a package B provides, or A's curated
`consumes` starts with `B:`.

A card is STALE when the repo is checked out where the card says
(`local_path`, relative to the base) and its HEAD moved since the sync.
`?` in `list` means the repo is not checked out next to the base.

## Hooks

With the plugin (only while greenhouse is on):

- `hook session-start`: with a base, a platform overview (about 400 tokens
  at most: repo one-liners, this repo's links, a sync reminder); without
  one, the ask-for-a-base instruction above.
- `hook session-end`: re-harvests this repo's card (from a folder of
  repos: the card of every repo the session's tool calls touched) in a
  detached background process, only if the base opted in with
  `bin/greenhouse config auto_sync true` (stored in `greenhouse.json`, so
  it holds for every repo of the base). Curated fields are kept. Off by
  default because it writes to a shared folder without being asked.

Copied skill: add to `.claude/settings.json`:
```json
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/greenhouse hook session-start", "timeout": 5}]}],
  "SessionEnd": [{"hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/greenhouse hook session-end", "timeout": 5}]}]
}}
```

## The envoy agent

`agents/envoy.md` (shipped with the plugin; copied skill: put it in
`.claude/agents/`) answers one question about another repo's checkout,
read-only, and returns file:line findings plus a proposed platform note.
The parent records the note with `note`, so only the parent writes to the
base.

## Sharing the base

Make the base its own git repository (or a submodule) and commit
everything except `.lock` and `*.tmp` (its `.gitignore` is created once).
Concurrent local writes are serialized with a lock; across machines, git
merges cards cleanly because each repo writes only its own
`repos/<slug>.json`. `INDEX.md` is regenerated on every write; on a merge
conflict take either side and run any write command (e.g. `sync`).

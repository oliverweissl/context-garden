---
name: mycelium
description: Decide whether to spawn subagents (one, or several in parallel) to search, investigate or edit, and brief them well; passes finding notes between agents so they don't redo the same exploration. Use before delegating to an agent and after an agent reports findings.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/bin/mycelium *)
---

# mycelium

!`${CLAUDE_SKILL_DIR}/bin/mycelium mode --banner`

Delegation costs a fresh context per agent. Default to doing the work
yourself: a single file lookup, a small edit or one test command never
needs an agent.

`mycelium` is not on PATH: always use the full `<this-skill-dir>/bin/mycelium`
path. Notes live in `<git root>/.mycelium/notes.json`.

**Before spawning an agent**

1. Delegate only a bounded task with a finish condition. Prefer one agent;
   run agents in parallel only for independent tasks.
2. Check shared notes: `<this-skill-dir>/bin/mycelium brief "<task>"`.
3. Write the brief with these lines (the plugin's gate hook denies an
   Agent call that lacks them and tells you what's missing):
   ```
   Task: <one question or change, with a finish condition>
   Why delegate: <why the main thread would be less efficient>
   Known: <relevant notes from `brief`, or "none relevant">
   Return: <findings with file:line, answer, unresolved questions>
   Budget: <max searches/turns; on exhaustion return partial findings>
   ```
4. Never spawn a second agent for the same investigation: reuse the first
   result or continue that agent. The gate denies near-duplicates unless a
   `Not a duplicate: <how>` line explains the difference.
5. For code search use a read-only agent (`scout`, installed by
   `bin/mycelium install-agents`); use an editing agent only when it owns a
   distinct change.

**After an agent returns**

Only you (the parent) write notes, so parallel agents never overwrite each
other. Record each durable finding once, with the files it describes:
```
<this-skill-dir>/bin/mycelium note "<subsystem>" "<finding>" <file>...
```
Notes are hashed against their files; `bin/mycelium list --stale` shows
notes whose files changed. Re-check, then `bin/mycelium verify <id>`, or
`bin/mycelium drop <id>`. Keep task status out of notes (or use
`--kind status`, which is never briefed).

Gate modes, hook setup, note format: `references/protocol.md`.

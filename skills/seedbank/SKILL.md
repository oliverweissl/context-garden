---
name: seedbank
description: Maintain a small, generated AGENTS.md of repository facts (build commands, conventions, invariants, past mistakes) that were rediscovered often or were costly to get wrong. Use at task start to check cached facts, and at task end to log and promote what you had to dig for.
---

# seedbank

Offline fact store + profiler. It counts rediscoveries and mistake cost;
you supply the fact text and decide what to promote. Nothing is promoted
from a single sighting. Formulas: `references/scoring.md`.

`seedbank` is not on PATH: run `<this-skill-dir>/bin/seedbank`. State
lives in `<git repo root>/.seedbank/`.

1. **Start:** read `AGENTS.md`. If it answers your question, skip
   re-discovery.
2. **While working**, log only "how does this repo work" discoveries, not
   every file you touch (with the plugin hook, Read/Grep/Glob are logged
   automatically):
   ```
   bin/seedbank observe run --scope <topic> -- <command...>
   bin/seedbank observe mistake "<rule you broke>" --scope invariants [--source <file>]
   bin/seedbank observe fact "<invariant>" --scope invariants
   ```
3. **End:** `bin/seedbank candidates`, then
   ```
   bin/seedbank promote <key> --representation "<compact text>" --tier hot [--critical]
   bin/seedbank promote <key> --tier warm --scope <topic>
   bin/seedbank compile
   ```
   Use `--critical` for correctness/safety invariants (never evicted).
   Promote refuses a fact contradicting one in the same scope; re-run with
   `--replace <id>` or `--force`. `compile` rewrites only the region
   between `<!-- seedbank:begin -->`/`<!-- seedbank:end -->` and drops
   facts whose source changed (critical ones stay, marked "verify").
4. Existing hand-written `AGENTS.md`/`CLAUDE.md`: `bin/seedbank import <path>`.

Hook setup, tiers, what to commit, guarantees: `references/guide.md`.
Full CLI: `references/schema.md`.

---
description: Turn on the context-garden skills that fit how you work (all start off)
argument-hint: "[solo|platform|swarm|platform-swarm|logs|all|off] [--scope local|project|user]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/hooks/garden.py *)
---

# context-garden setup

Every context-garden skill starts `off`. Help the user turn on the right ones.
The helper is `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/garden.py` (not on PATH; always
use this full path).

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/garden.py status` and
   `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/garden.py presets`.
2. If the arguments below already name a preset (and optionally `--scope`), apply
   it and skip to step 5.
3. Otherwise ask the user (AskUserQuestion, one question per item):
   - How do they work? Offer the presets, recommending from what you can see:
     - `solo`: one repository, everyday coding and debugging.
     - `platform`: a big project split over several repos that interact
       (services, shared packages, APIs); adds greenhouse.
     - `swarm`: tasks where many subagents search, investigate or edit in
       parallel; adds mycelium.
     - `platform-swarm`: both of the above.
     - `logs`: only long builds/tests/CI/HPC output (compost alone).
     Mention `all` and `off` only if asked.
   - Scope: `local` (this repo, only them; default), `project` (this repo,
     shared with the team via `.claude/settings.json`), `user` (every repo).
4. Apply: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/garden.py preset <name> --scope <scope>`.
   For a single change: `... garden.py set <skill> <on|manual|off> --scope <scope>`.
5. Show the resulting status and say in two or three lines what is now on and
   what it does; skills set to `manual` run only when called, e.g.
   `/context-garden:weeder slim skills/foo`. Changes apply from the next prompt.

Arguments: $ARGUMENTS

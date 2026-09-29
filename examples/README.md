# Examples

Before/after token counts (`chars/4` estimate, as in each Skill's CLI). Skill cost = description + `SKILL.md` body from `skills/weeder/scripts/weeder.py audit`.

## ♻️ compost example
**Before — 4,327 tokens**

```text
examples/flaky_service/test_service.py::test_process_returns_ok[0] FAILED [  4%]
examples/flaky_service/test_service.py::test_process_returns_ok[1] FAILED [  9%]
... (13 more identical FAILED lines) ...

=================================== FAILURES ===================================
__________________________ test_process_returns_ok[0] __________________________
    def process(item: dict) -> dict:
        timeout = get_timeout(item.get("kind", "default"))
        if not isinstance(timeout, int):
>           raise TypeError(f"timeout must be an int, got {timeout!r}")
E           TypeError: timeout must be an int, got '30'
... (repeated, near-verbatim, 14 more times) ...
```

**After — 120 tokens**

```text
Root error(s):
  TypeError: timeout must be an int, got '30'
    (lines 390-404, x15, affected tests: 15)

Secondary failure groups:
  - KeyError: 'urgent' x2

Numerical summary:
  tests_passed: 4
  tests_failed: 17
```

**Saving: 97.2%**

> [!NOTE]
> Exact raw output remains available through `compost get`, `compost event`, and `compost grep`.

> [!WARNING]
> Loading the Skill costs ~395 tokens: worth it for repetitive output or commands you will re-run.

## ✂️ pruner example

```bash
python3 skills/pruner/scripts/pruner.py index

python3 skills/pruner/scripts/pruner.py select \
  --budget 3000 \
  --task \
  "seedbank compile leaves a stale .seedbank/warm/<scope>.md file behind \
   when a scope's last warm fact gets demoted -- fix compile_outputs to delete it"
```

**Before — 8,814 tokens**

All four `skills/seedbank/scripts/*.py` files.

**After — 2,992 tokens**

```text
Required context (5):
  skills/seedbank/scripts/sb_compile.py:57-97  [function, score=13.5, ~431tok]
      - task text directly names 'compile_outputs'
      - docstring shares keyword(s) ['file', 'md', 'warm'] with task
  ...
used: 2992 tokens
```

**Saving: 66.0%**

Including the ~470-token Skill cost, first use is 3,462 tokens (61% saving).

> [!WARNING]
> If the right file is already obvious, reading it directly is cheaper.

## 🌰 seedbank example

**Before — 1,641 tokens**: re-reading `docs/skill-development.md`, `docs/architecture.md`, `skills/weeder/SKILL.md` each session.

**After — 136 tokens**

```text
- Validate every Skill's structure with `scripts/validate-skills`.
  (source: docs/skill-development.md, scripts/validate-skills)
- Self-containment: a Skill must never reference a relative path outside
  its own directory, e.g. `../../benchmarks/...`.
  (source: docs/architecture.md, "Skill self-containment (critical rule)")
- Accept a weeder rewrite only if routing accuracy after >= before
  (minus a small tolerance) AND constraint preservation is 100%.
  (source: skills/weeder/SKILL.md, workflow step 7)
```

**Saving: 91.7% per later session**

> [!NOTE]
> The ~507-token Skill loads only while curating facts (`observe`/`promote`/`compile`); consumers of the generated `AGENTS.md` pay only for its hot section.

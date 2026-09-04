# Report schema and CLI reference

## CheckResult

Every check function returns one of these (`trellis/schema.py`):

```yaml
name:        # e.g. "residual_norm:solver_residual" -- module-qualified by convention
status:      # PASS | WARN | FAIL
category:    # implementation | numerical | model_validation | empirical
metric:      # dict of the raw computed numbers
expected:    # what was required to PASS
observed:    # what was actually measured
evidence:    # supporting raw data (arrays, samples, ...) -- truncated where large
notes:       # human-readable explanation, always populated for WARN/FAIL
```

`metric`/`expected`/`observed`/`evidence` are passed through
`_util.to_jsonable` automatically, so numpy scalars/arrays in them become
plain Python floats/ints/lists — every CheckResult is JSON-serializable
without extra handling in your spec script.

### The four categories (the "Important Rule")

- **implementation** — the code behaves as coded (reproducibility,
  parameter sanity, optimizer termination flags). This is what ordinary
  unit tests already give you; trellis checks in this category exist
  mainly to make that boundary explicit, not to replace your test suite.
- **numerical** — the numerics are internally self-consistent: residuals
  small, convergence order matches theory, matrices well-conditioned,
  gradients match their finite-difference check. This is trellis's core.
- **model_validation** — compared against a *known-correct* answer
  (an analytic solution, a manufactured solution, a reference
  implementation). Numerically self-consistent code can still validate
  the wrong model; this tier is what actually rules that out.
- **empirical** — validated across multiple random trials/seeds, with a
  confidence interval, not asserted from one run.

## Report

```yaml
status:              # worst of any individual check's status (FAIL > WARN > PASS)
checks: [CheckResult, ...]
unsupported_claims:  # claims this report does NOT support making
remaining_risks:      # known gaps in what was verified
created_at:
```

`build_report(results, unsupported_claims=None, remaining_risks=None, auto_gaps=True)`
(`trellis.build_report`, re-exported from `trellis.schema`) builds this
from a list of `CheckResult`. With `auto_gaps=True` (the default), it
inspects which `category` values are present across `results` and appends
a `remaining_risks`/`unsupported_claims` entry for each tier that's
**entirely absent** — see `SKILL.md`'s point about this not being
something you have to remember to write yourself. Pass `auto_gaps=False`
to suppress this if you have a specific reason to (e.g. a spec that is
deliberately implementation-tier-only and you don't want the reminder
repeated every run).

`Report.render_human()` — the text format the CLI prints by default.
`Report.to_dict()` — the JSON-serializable form (`--json` / `.save()`).
`Report.save(path)` — writes the JSON form, creating parent dirs.
`Report.exit_code()` — `1` if `status == "FAIL"`, else `0`.

## CLI (`scripts/trellis.py`)

### `trellis run <spec.py> [--save PATH] [--json]`

Executes `spec.py` as a Python module (so its top-level code — which
calls check functions and builds the `RESULTS` list — runs). Requires the
spec to define a module-level `RESULTS: list[CheckResult]`; optionally
`UNSUPPORTED_CLAIMS: list[str]` and `REMAINING_RISKS: list[str]` for gaps
you already know about beyond what `auto_gaps` adds automatically. Prints
the report, saves it to `--save` (default:
`<spec_dir>/.trellis/<spec_stem>_report.json`), exits via
`Report.exit_code()`.

### `trellis list-modules`

Lists every public check function per domain module with its first
docstring line — a quick reference for "what's available", filtered to
each module's own functions (helpers re-exported via
`from .x import y`, like the shared `convergence_check`, are excluded so
this doesn't imply e.g. `ode.threshold_status` is itself a check).

### `trellis show <report.json>`

Re-prints a previously saved report without re-running anything.

## Writing a spec script

A spec script is not a special format — it's a normal Python script. The
only contract is the module-level `RESULTS` list. This means:

- You can compute intermediate values, load data from files, call your
  actual project code, and use ordinary Python control flow before
  building `RESULTS` — nothing here is a restricted DSL.
- Environment variables, CLI args (via `sys.argv`), or config files are
  entirely your choice for parameterizing a spec (see
  `tests/fixtures/uc1_pde_stencil_regression.py`'s use of
  `SENTINEL_UC1_BUGGED` to toggle between the buggy and healthy scheme for
  testing purposes).
- Nothing stops you from also using compost/seedbank alongside a
  spec script for a numerical experiment that itself produces large
  output.

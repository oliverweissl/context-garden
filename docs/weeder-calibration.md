# Weeder: token calibration and routing validation

Weeder is offline and deterministic. It estimates tokens from character
counts and predicts routing with a lexical proxy
(`skills/weeder/references/routing-heuristic.md`). Two manual scripts
check those approximations against the real `claude` CLI:

| Script | Checks | Writes | Cost |
|---|---|---|---|
| `scripts/calibrate-tokens` | chars-per-token ratios | `skills/weeder/references/token-calibration.json` | 2 short headless sessions per sample (~20 samples, so ~40 sessions) |
| `scripts/validate-routing` | the lexical routing proxy vs real Skill routing | stdout, plus optional `--json-out` | about 1 short turn per prompt |

Both use your logged-in `claude` CLI (your own Claude Code usage) and
never need an Anthropic API key. Neither uses `--bare`, because that flag
forces API-key auth. They are **manual only**: CI, the test suite, and
weeder itself never run them; at runtime weeder only reads the
calibration JSON. Optional flags are passed only if `claude --help` lists
them, so `--dry-run` shows the exact command for your CLI version without
making any calls.

## Token calibration

```bash
scripts/calibrate-tokens --dry-run   # list samples + the exact command
scripts/calibrate-tokens             # measure, write token-calibration.json
scripts/calibrate-tokens --model sonnet --limit 6   # cheaper partial run
```

Each sample (SKILL.md files, some docs, Python, and `task.yaml` files,
truncated to 6000 characters) is sent twice with an identical prefix and
a per-pair nonce: once with an empty `<sample></sample>` wrapper, once
with the sample inside. Its token count is the difference in prompt
tokens (`input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`, a sum independent of cache state).

Fenced code is stripped from markdown samples, since weeder estimates it
with the separate `code` ratio.

**Effect on weeder.** When `token-calibration.json` exists, `we_tokens.py`
applies its ratios per category:

- the description uses the `yaml` ratio
- fenced code uses the `code` ratio
- everything else uses the `markdown` ratio

Reports then print `estimated (calibrated YYYY-MM-DD); observed
per-sample error X% to Y%`. Without the file, reports print `estimated
(uncalibrated chars/4)`. To force either mode, set
`WEEDER_TOKEN_CALIBRATION=<path>`. A nonexistent path forces chars/4.

**When to re-run.**

- After a Claude Code or model upgrade. Tokenizers can change between
  model families, and the file records the version it was measured with.
- When you switch weeder's audience to a different model (use `--model`).
- When the skills' content style changes a lot, for example much more
  code in SKILL.md files.

Commit the resulting JSON so other users get the same ratios.

## Routing validation

```bash
scripts/validate-routing --dry-run            # weeder's fixture pair, no calls
scripts/validate-routing                      # bloated_skill vs competing_skill
scripts/validate-routing --skill skills/pruner \
  --examples my_pruner_routing.json --competing skills/compost skills/seedbank \
  --json-out /tmp/pruner-routing.json
```

The examples file uses weeder's `test-routing` format (default
`skills/weeder/tests/fixtures/routing_examples.json`). Each prompt runs
in a temp project with the skill under test and every `--competing` skill
installed, restricted to `--tools Skill` so the model can route but not
act. The first `Skill` call is compared with weeder's proxy prediction
(`we_routing.predict_trigger`), scored with the same rules as `weeder
test-routing`. The script exits nonzero if any prompt errored.

**Caveats.**

- Skills in `~/.claude/skills` still load and show up as extra columns;
  see [benchmarking isolation](benchmarking.md#isolation-between-arms).
- Real routing is stochastic. Re-run borderline prompts before drawing
  conclusions from a single flip.
- If your CLI names the tool differently, override it with
  `--tools <names>`.

# Weeder: token calibration and routing validation

Weeder is offline and deterministic. It estimates tokens from character
counts and predicts routing with a lexical proxy
(`skills/weeder/references/routing-heuristic.md`). Two manual scripts
check those approximations against the real `claude` CLI:

| Script | Checks | Writes | Cost |
|---|---|---|---|
| `scripts/calibrate-tokens` | chars-per-token ratios | `skills/weeder/references/token-calibration.json` | 2 short headless sessions per sample (~20 samples, so ~40 sessions) |
| `scripts/validate-routing` | the lexical routing proxy vs real Skill routing | stdout, plus optional `--json-out` | about 1 short turn per prompt |

Both scripts call your logged-in `claude` CLI, so the runs count against
your own Claude Code usage (subscription or whatever `claude` is
authenticated with). Neither script reads or needs an Anthropic API key.
Neither uses `--bare`, because that flag forces API-key auth. They are
**manual only**: CI and the test suite never run them. Weeder never calls
them, and at runtime it only reads the calibration JSON.

Both scripts read `claude --help` and pass an optional flag only when
your CLI version lists it. Pass `--dry-run` to either script to see the
exact command, the samples or prompts, and (for routing) the proxy's
predictions, without making any calls.

## Token calibration

```bash
scripts/calibrate-tokens --dry-run   # list samples + the exact command
scripts/calibrate-tokens             # measure, write token-calibration.json
scripts/calibrate-tokens --model sonnet --limit 6   # cheaper partial run
```

**What it does.** The samples are:

- the frontmatter and body of every `skills/*/SKILL.md`
- a few docs pages
- a few Python files
- two benchmark `task.yaml` files

Each sample is truncated to 6000 characters. Fenced code is removed from
markdown samples because weeder estimates fenced code at the `code` ratio.

The script runs each sample twice, in an empty temp directory:

1. a minimal prompt with an empty `<sample></sample>` wrapper
2. the same prompt with the sample inside the wrapper

Both calls use `claude -p --output-format json`, and each prompt is
passed on stdin. The sample's token count is the difference in prompt
tokens between the two calls. Prompt tokens are
`usage.input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
These three fields are disjoint parts of the same prompt, so their sum
doesn't depend on cache state. Each pair gets its own random nonce. The
prefix is identical inside a pair, so the difference is exactly the
sample, and no cache entry is shared across pairs.

These flags keep the fixed overhead small and avoid side effects:

- `--tools ""`: no tools, and a guaranteed single turn
- `--setting-sources project`: no user plugins or hooks
- `--strict-mcp-config`: no MCP servers
- `--disable-slash-commands`: no skills are listed
- `--no-session-persistence`: no saved session
- `--permission-prompts none`

The script then computes these values per category (`markdown`, `code`,
`yaml`):

- aggregate chars/token
- the signed per-sample error of that ratio against the measured count
- the same error for plain chars/4, for comparison

The JSON also records the date, the `claude --version` output, the model
and every sample's measurement.

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

**What it does.** The examples file uses weeder's format
(`{"positive": [...], "negative": [...], "ambiguous": [{"prompt":,
"expected":}]}`). The default is
`skills/weeder/tests/fixtures/routing_examples.json`. For each prompt, the
script:

1. Creates a temp project and copies the skill under test and every
   `--competing` skill into `.claude/skills/<name>/`.
2. Runs `claude -p --output-format stream-json --verbose` with these
   flags:
   - `--tools Skill`: the model can route but can't act
   - `--setting-sources project`, so user plugins' skills don't compete
   - `--strict-mcp-config`
   - `--no-session-persistence`
   - `--permission-prompts none`
   - `--max-budget-usd 0.25`
   - a nonce in `--append-system-prompt`
3. Reads the event stream and records the first `Skill` tool_use. It
   stops the session as soon as a `Skill` call appears, or when the model
   answers without one.
4. Computes weeder's proxy prediction (`we_routing.predict_trigger`)
   against the same installed descriptions.

**Report.** The script prints:

- real-routing accuracy and proxy accuracy, scored with the same rules
  as `weeder test-routing`: negatives are correct unless the skill under
  test fires, and `either` is always correct
- real/proxy agreement, the fraction of prompts where both picked the
  same skill or both picked none
- a confusion table, with rows for the real Skill call and columns for
  the proxy prediction
- every prompt where the two disagree

The script exits nonzero if any prompt errored.

**Caveats.**

- Skills placed directly in `~/.claude/skills` aren't governed by
  `--setting-sources`. Keep that directory empty, or expect those skills
  to show up as extra columns.
- Real routing is stochastic. Re-run borderline prompts before drawing
  conclusions from a single flip.
- If your CLI names the tool differently, override it with
  `--tools <names>`.

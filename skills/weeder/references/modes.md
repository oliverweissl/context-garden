# Modes reference

`audit` (measure) · `optimize` (mechanical refactor) · `suggest` /
`apply-suggestion` (structure and apply step 3's judgment call, gated by
LLM-assist level) · `test-routing` · `test-function` · `diff` (combined
before/after report). Full flag reference: `references/classification.md`
(what audit measures and how), `references/llm-assist.md` (assist levels,
request/answer schemas), and `references/routing-heuristic.md` (what the
two proxy checks do and don't guarantee).

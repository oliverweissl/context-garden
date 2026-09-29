# Published benchmark results

One folder per released version, created with
`scripts/benchmark plot <results-dir> --publish` (see [`../benchmarking.md`](../benchmarking.md)).
The top-level README always shows the most recently published version.

| Version | Arms | n per task × arm | Notes |
|---|---|---|---|
| [v0.1.0](v0.1.0/summary.md) | baseline, treatment | 5 | Pre-0.2 harness: baseline working copies could still contain fixture answers, runs were not interleaved, and compost/pruner/seedbank were never actually invoked in the treatment transcripts. Treat as indicative only. |
| [v0.2.0](v0.2.0/summary.md) | baseline, treatment-natural, treatment-forced | 8 | **Invalid.** `--permission-mode acceptEdits` auto-denied every headless Bash call (0/14 skill CLI calls ran, pytest was denied in all arms), and 22/72 trials were zero-token infra failures scored as FAILs. Kept for the record only. |

# Benchmark summary

30 record(s); schedule seed(s): n/a. Intervals are 95% CIs (pass rate: Wilson; pass-rate delta: Newcombe; means and mean deltas: t / Welch t).

## Primary: verified pass rate

| task | arm | n | pass | pass rate [95% CI] | Δ vs baseline [95% CI] | skill invoked |
|---|---|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 5 | 5/5 | 100% [57%, 100%] | — | n/a |
| compost-noisy-failure-cluster | treatment | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | n/a |
| pruner-file-tokenusage-bug | baseline | 5 | 5/5 | 100% [57%, 100%] | — | n/a |
| pruner-file-tokenusage-bug | treatment | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | n/a |
| seedbank-recurring-facts | baseline | 5 | 1/5 | 20% [4%, 62%] | — | n/a |
| seedbank-recurring-facts | treatment | 5 | 0/5 | 0% [0%, 43%] | -20% [-62%, 26%] | n/a |

## Secondary: tokens and cost

| task | arm | tokens mean [95% CI] | Δ tokens vs baseline [95% CI] | Δ % | cost $ mean [95% CI] | tokens, skill invoked | tokens, not invoked |
|---|---|---:|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 18269 [17818, 18719] | — | — | 0.127 [0.113, 0.141] | — (n=0) | — (n=0) |
| compost-noisy-failure-cluster | treatment | 17484 [16737, 18232] | -784 [-1539, -29] | -4.3% [-8.4%, -0.2%] | 0.123 [0.113, 0.133] | — (n=0) | — (n=0) |
| pruner-file-tokenusage-bug | baseline | 16348 [15699, 16996] | — | — | 0.112 [0.104, 0.121] | — (n=0) | — (n=0) |
| pruner-file-tokenusage-bug | treatment | 16258 [15452, 17065] | -90 [-957, 778] | -0.5% [-5.9%, 4.8%] | 0.117 [0.099, 0.134] | — (n=0) | — (n=0) |
| seedbank-recurring-facts | baseline | 20473 [15944, 25002] | — | — | 0.127 [0.090, 0.164] | — (n=0) | — (n=0) |
| seedbank-recurring-facts | treatment | 19880 [18230, 21530] | -593 [-5048, 3862] | -2.9% [-24.7%, 18.9%] | 0.115 [0.101, 0.129] | — (n=0) | — (n=0) |

## Routing: does the agent pick the skill up unprompted?

| task | arm | natural invocation rate [95% CI] |
|---|---|---:|
| compost-noisy-failure-cluster | treatment | n/a |
| pruner-file-tokenusage-bug | treatment | n/a |
| seedbank-recurring-facts | treatment | n/a |

# Benchmark summary

75 record(s) (0 infra-error trial(s) excluded); schedule seed(s): 0. Intervals are 95% CIs (pass rate: Wilson; pass-rate delta: Newcombe; means and mean deltas: t / Welch t).

## Primary: verified pass rate

| task | arm | n | pass | pass rate [95% CI] | Δ vs baseline [95% CI] | skill invoked |
|---|---|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 5 | 5/5 | 100% [57%, 100%] | — | 0/5 |
| compost-noisy-failure-cluster | treatment-natural | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |
| compost-noisy-failure-cluster | treatment-forced | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 5/5 |
| mycelium-notes-reuse | baseline | 5 | 5/5 | 100% [57%, 100%] | — | 0/5 |
| mycelium-notes-reuse | treatment-natural | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |
| mycelium-notes-reuse | treatment-forced | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 5/5 |
| mycelium-overlapping-investigation | baseline | 5 | 5/5 | 100% [57%, 100%] | — | 0/5 |
| mycelium-overlapping-investigation | treatment-natural | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |
| mycelium-overlapping-investigation | treatment-forced | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 5/5 |
| pruner-file-tokenusage-bug | baseline | 5 | 5/5 | 100% [57%, 100%] | — | 0/5 |
| pruner-file-tokenusage-bug | treatment-natural | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |
| pruner-file-tokenusage-bug | treatment-forced | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 5/5 |
| seedbank-recurring-facts | baseline | 5 | 5/5 | 100% [57%, 100%] | — | 0/5 |
| seedbank-recurring-facts | treatment-natural | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |
| seedbank-recurring-facts | treatment-forced | 5 | 5/5 | 100% [57%, 100%] | +0% [-43%, 43%] | 0/5 |

## Secondary: tokens and cost

| task | arm | tokens mean [95% CI] | Δ tokens vs baseline [95% CI] | Δ % | cost $ mean [95% CI] | tokens, skill invoked | tokens, not invoked |
|---|---|---:|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 9400 [9215, 9586] | — | — | 0.102 [0.100, 0.103] | — (n=0) | 9400 [9215, 9586] (n=5) |
| compost-noisy-failure-cluster | treatment-natural | 9437 [9314, 9560] | +36 [-154, 226] | +0.4% [-1.6%, 2.4%] | 0.102 [0.101, 0.103] | — (n=0) | 9437 [9314, 9560] (n=5) |
| compost-noisy-failure-cluster | treatment-forced | 10952 [10762, 11143] | +1552 [1331, 1773] | +16.5% [14.2%, 18.9%] | 0.119 [0.116, 0.122] | 10952 [10762, 11143] (n=5) | — (n=0) |
| mycelium-notes-reuse | baseline | 22893 [20769, 25016] | — | — | 0.249 [0.228, 0.270] | — (n=0) | 22893 [20769, 25016] (n=5) |
| mycelium-notes-reuse | treatment-natural | 24608 [22170, 27045] | +1715 [-980, 4411] | +7.5% [-4.3%, 19.3%] | 0.270 [0.243, 0.297] | — (n=0) | 24608 [22170, 27045] (n=5) |
| mycelium-notes-reuse | treatment-forced | 23546 [23120, 23971] | +653 [-1462, 2768] | +2.9% [-6.4%, 12.1%] | 0.262 [0.253, 0.270] | 23546 [23120, 23971] (n=5) | — (n=0) |
| mycelium-overlapping-investigation | baseline | 12255 [10480, 14030] | — | — | 0.132 [0.116, 0.149] | — (n=0) | 12255 [10480, 14030] (n=5) |
| mycelium-overlapping-investigation | treatment-natural | 10203 [8909, 11498] | -2052 [-3909, -195] | -16.7% [-31.9%, -1.6%] | 0.111 [0.098, 0.125] | — (n=0) | 10203 [8909, 11498] (n=5) |
| mycelium-overlapping-investigation | treatment-forced | 12299 [11767, 12831] | +43 [-1712, 1799] | +0.4% [-14.0%, 14.7%] | 0.137 [0.128, 0.146] | 12299 [11767, 12831] (n=5) | — (n=0) |
| pruner-file-tokenusage-bug | baseline | 10106 [8961, 11250] | — | — | 0.115 [0.099, 0.132] | — (n=0) | 10106 [8961, 11250] (n=5) |
| pruner-file-tokenusage-bug | treatment-natural | 10146 [8924, 11367] | +40 [-1351, 1432] | +0.4% [-13.4%, 14.2%] | 0.118 [0.099, 0.137] | — (n=0) | 10146 [8924, 11367] (n=5) |
| pruner-file-tokenusage-bug | treatment-forced | 12010 [10295, 13725] | +1904 [146, 3662] | +18.8% [1.4%, 36.2%] | 0.144 [0.115, 0.173] | 12010 [10295, 13725] (n=5) | — (n=0) |
| seedbank-recurring-facts | baseline | 13315 [11644, 14985] | — | — | 0.140 [0.126, 0.155] | — (n=0) | 13315 [11644, 14985] (n=5) |
| seedbank-recurring-facts | treatment-natural | 7982 [7799, 8164] | -5333 [-7002, -3664] | -40.1% [-52.6%, -27.5%] | 0.083 [0.082, 0.085] | — (n=0) | 7982 [7799, 8164] (n=5) |
| seedbank-recurring-facts | treatment-forced | 6867 [6839, 6895] | -6448 [-8118, -4777] | -48.4% [-61.0%, -35.9%] | 0.068 [0.068, 0.069] | — (n=0) | 6867 [6839, 6895] (n=5) |

## Routing: does the agent pick the skill up unprompted?

| task | arm | natural invocation rate [95% CI] |
|---|---|---:|
| compost-noisy-failure-cluster | treatment-natural | 0% [0%, 43%] (0/5) |
| mycelium-notes-reuse | treatment-natural | 0% [0%, 43%] (0/5) |
| mycelium-overlapping-investigation | treatment-natural | 0% [0%, 43%] (0/5) |
| pruner-file-tokenusage-bug | treatment-natural | 0% [0%, 43%] (0/5) |
| seedbank-recurring-facts | treatment-natural | 0% [0%, 43%] (0/5) |

## Benefit: treatment-forced vs baseline

| task | Δ pass rate [95% CI] | Δ tokens [95% CI] | Δ % | forced invocation rate |
|---|---:|---:|---:|---:|
| compost-noisy-failure-cluster | +0% [-43%, 43%] | +1552 [1331, 1773] | +16.5% [14.2%, 18.9%] | 100% [57%, 100%] |
| mycelium-notes-reuse | +0% [-43%, 43%] | +653 [-1462, 2768] | +2.9% [-6.4%, 12.1%] | 100% [57%, 100%] |
| mycelium-overlapping-investigation | +0% [-43%, 43%] | +43 [-1712, 1799] | +0.4% [-14.0%, 14.7%] | 100% [57%, 100%] |
| pruner-file-tokenusage-bug | +0% [-43%, 43%] | +1904 [146, 3662] | +18.8% [1.4%, 36.2%] | 100% [57%, 100%] |
| seedbank-recurring-facts | +0% [-43%, 43%] | -6448 [-8118, -4777] | -48.4% [-61.0%, -35.9%] | 0% [0%, 43%] |

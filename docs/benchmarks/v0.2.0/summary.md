# Benchmark summary

72 record(s); schedule seed(s): 0. Intervals are 95% CIs (pass rate: Wilson; pass-rate delta: Newcombe; means and mean deltas: t / Welch t).

## Primary: verified pass rate

| task | arm | n | pass | pass rate [95% CI] | Δ vs baseline [95% CI] | skill invoked |
|---|---|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 8 | 5/8 | 62% [31%, 86%] | — | 0/8 |
| compost-noisy-failure-cluster | treatment-natural | 8 | 5/8 | 62% [31%, 86%] | +0% [-40%, 40%] | 0/8 |
| compost-noisy-failure-cluster | treatment-forced | 8 | 6/8 | 75% [41%, 93%] | +12% [-29%, 49%] | 6/8 |
| pruner-file-tokenusage-bug | baseline | 8 | 6/8 | 75% [41%, 93%] | — | 0/8 |
| pruner-file-tokenusage-bug | treatment-natural | 8 | 6/8 | 75% [41%, 93%] | +0% [-38%, 38%] | 0/8 |
| pruner-file-tokenusage-bug | treatment-forced | 8 | 5/8 | 62% [31%, 86%] | -12% [-49%, 29%] | 5/8 |
| seedbank-recurring-facts | baseline | 8 | 5/8 | 62% [31%, 86%] | — | 0/8 |
| seedbank-recurring-facts | treatment-natural | 8 | 5/8 | 62% [31%, 86%] | +0% [-40%, 40%] | 0/8 |
| seedbank-recurring-facts | treatment-forced | 8 | 6/8 | 75% [41%, 93%] | +12% [-29%, 49%] | 0/8 |

## Secondary: tokens and cost

| task | arm | tokens mean [95% CI] | Δ tokens vs baseline [95% CI] | Δ % | cost $ mean [95% CI] | tokens, skill invoked | tokens, not invoked |
|---|---|---:|---:|---:|---:|---:|---:|
| compost-noisy-failure-cluster | baseline | 7036 [3142, 10930] | — | — | 0.081 [0.034, 0.128] | — (n=0) | 7036 [3142, 10930] (n=8) |
| compost-noisy-failure-cluster | treatment-natural | 6586 [2023, 11150] | -450 [-5904, 5004] | -6.4% [-83.9%, 71.1%] | 0.077 [0.024, 0.131] | — (n=0) | 6586 [2023, 11150] (n=8) |
| compost-noisy-failure-cluster | treatment-forced | 9548 [4587, 14508] | +2512 [-3238, 8261] | +35.7% [-46.0%, 117.4%] | 0.134 [0.059, 0.209] | 12730 [11876, 13584] (n=6) | 0 [0, 0] (n=2) |
| pruner-file-tokenusage-bug | baseline | 7463 [3599, 11327] | — | — | 0.084 [0.041, 0.128] | — (n=0) | 7463 [3599, 11327] (n=8) |
| pruner-file-tokenusage-bug | treatment-natural | 7454 [3603, 11306] | -8 [-4957, 4940] | -0.1% [-66.4%, 66.2%] | 0.083 [0.040, 0.126] | — (n=0) | 7454 [3603, 11306] (n=8) |
| pruner-file-tokenusage-bug | treatment-forced | 7740 [2381, 13100] | +277 [-5772, 6326] | +3.7% [-77.3%, 84.8%] | 0.087 [0.027, 0.146] | 12385 [12280, 12489] (n=5) | 0 [0, 0] (n=3) |
| seedbank-recurring-facts | baseline | 9318 [2782, 15853] | — | — | 0.095 [0.028, 0.161] | — (n=0) | 9318 [2782, 15853] (n=8) |
| seedbank-recurring-facts | treatment-natural | 5007 [1538, 8477] | -4310 [-11226, 2605] | -46.3% [-120.5%, 28.0%] | 0.052 [0.016, 0.088] | — (n=0) | 5007 [1538, 8477] (n=8) |
| seedbank-recurring-facts | treatment-forced | 5231 [2526, 7937] | -4086 [-10818, 2645] | -43.9% [-116.1%, 28.4%] | 0.053 [0.025, 0.080] | — (n=0) | 5231 [2526, 7937] (n=8) |

## Routing: does the agent pick the skill up unprompted?

| task | arm | natural invocation rate [95% CI] |
|---|---|---:|
| compost-noisy-failure-cluster | treatment-natural | 0% [0%, 32%] (0/8) |
| pruner-file-tokenusage-bug | treatment-natural | 0% [0%, 32%] (0/8) |
| seedbank-recurring-facts | treatment-natural | 0% [0%, 32%] (0/8) |

## Benefit: treatment-forced vs baseline

| task | Δ pass rate [95% CI] | Δ tokens [95% CI] | Δ % | forced invocation rate |
|---|---:|---:|---:|---:|
| compost-noisy-failure-cluster | +12% [-29%, 49%] | +2512 [-3238, 8261] | +35.7% [-46.0%, 117.4%] | 75% [41%, 93%] |
| pruner-file-tokenusage-bug | -12% [-49%, 29%] | +277 [-5772, 6326] | +3.7% [-77.3%, 84.8%] | 62% [31%, 86%] |
| seedbank-recurring-facts | +12% [-29%, 49%] | -4086 [-10818, 2645] | -43.9% [-116.1%, 28.4%] | 0% [0%, 32%] |

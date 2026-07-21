# Eval results

Matching of system findings to human review comments is manual judgment, documented per case in `eval/judgments/` (SPEC §5.2). Precision counts `uncertain` verdicts against the system.

Recall is reported over **matchable** ground truth. Each case is reviewed at its `review_sha` — the commit reviewers most engaged with, before their feedback was applied (see the dataset files). An item is matchable when its flagged code is actually present at that commit, verified by code inspection during judging; issues raised against a different commit, or already fixed by `review_sha`, are marked non-matchable. Raw recall over all ground truth is shown for completeness. The prior evaluation at the final pre-merge head (where most issues were already fixed) is archived under `eval/_archive_final_head/`.

## Headline

| experiment | cases | recall (matchable GT) | raw recall | precision proxy | valid/judged | mean $/PR | mean s/PR |
|---|---|---|---|---|---|---|---|
| full | 15 | 61% (19/31) | 37% (19/52) | 66% | 120/181 | $0.51 | 165 |
| baseline | 15 | 45% (14/31) | 27% (14/52) | 62% | 73/118 | $0.24 | 46 |
| no-tools | 15 | 55% (17/31) | 33% (17/52) | 66% | 112/170 | $0.24 | 131 |

## Ablation A — multi-agent vs monolithic baseline

- Recall (matchable GT): 61% vs 45% (16% delta)
- Precision: 66% vs 62%
- Cost: $0.51 vs $0.24 per PR

## Ablation B — tool loops on vs off

- Recall (matchable GT): 61% vs 55% (6% delta)
- Precision: 66% vs 66%
- Cost: $0.51 vs $0.24 per PR

## Confidence calibration (full system)

| confidence | judged | valid | validity rate |
|---|---|---|---|
| 0.40-0.55 | 11 | 3 | 27% |
| 0.55-0.70 | 17 | 6 | 35% |
| 0.70-0.85 | 74 | 42 | 57% |
| 0.85-1.00 | 79 | 69 | 87% |

## Per-case: full

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 9/13 | $0.34 | 140 |
| flask_2031 | 2/2 (of 2) | 11/13 | $0.37 | 154 |
| flask_2570 | 1/3 (of 3) | 8/14 | $1.32 | 252 |
| flask_3059 | 0/1 (of 2) | 9/11 | $0.56 | 153 |
| httpx_1197 | 2/2 (of 4) | 5/10 | $0.37 | 174 |
| httpx_2278 | 1/2 (of 3) | 9/13 | $0.40 | 150 |
| httpx_2423 | 2/3 (of 5) | 4/6 | $0.47 | 181 |
| httpx_3139 | 1/1 (of 3) | 9/14 | $0.90 | 195 |
| httpx_887 | 2/2 (of 3) | 6/13 | $0.28 | 135 |
| requests_2523 | 1/2 (of 3) | 9/13 | $0.46 | 211 |
| requests_3366 | 1/2 (of 4) | 13/18 | $0.45 | 156 |
| requests_3655 | 1/3 (of 5) | 11/17 | $0.46 | 160 |
| requests_3984 | 1/1 (of 4) | 4/9 | $0.39 | 119 |
| requests_4718 | 2/4 (of 4) | 4/6 | $0.25 | 87 |
| requests_5856 | 2/3 (of 4) | 9/11 | $0.57 | 210 |

## Per-case: baseline

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 6/8 | $0.16 | 44 |
| flask_2031 | 2/2 (of 2) | 5/8 | $0.13 | 39 |
| flask_2570 | 1/3 (of 3) | 7/10 | $0.81 | 75 |
| flask_3059 | 0/1 (of 2) | 3/7 | $0.29 | 36 |
| httpx_1197 | 1/2 (of 4) | 2/6 | $0.12 | 33 |
| httpx_2278 | 1/2 (of 3) | 3/9 | $0.22 | 46 |
| httpx_2423 | 2/3 (of 5) | 5/9 | $0.16 | 60 |
| httpx_3139 | 1/1 (of 3) | 8/9 | $0.38 | 39 |
| httpx_887 | 0/2 (of 3) | 3/7 | $0.13 | 52 |
| requests_2523 | 1/2 (of 3) | 4/8 | $0.27 | 49 |
| requests_3366 | 1/2 (of 4) | 9/9 | $0.18 | 71 |
| requests_3655 | 0/3 (of 5) | 7/10 | $0.16 | 41 |
| requests_3984 | 1/1 (of 4) | 1/5 | $0.12 | 36 |
| requests_4718 | 2/4 (of 4) | 4/5 | $0.24 | 26 |
| requests_5856 | 1/3 (of 4) | 6/8 | $0.29 | 43 |

## Per-case: no-tools

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 1/1 | $0.19 | 85 |
| flask_2031 | 2/2 (of 2) | 9/10 | $0.19 | 105 |
| flask_2570 | 1/3 (of 3) | 9/15 | $0.35 | 179 |
| flask_3059 | 0/1 (of 2) | 9/12 | $0.22 | 106 |
| httpx_1197 | 1/2 (of 4) | 7/17 | $0.24 | 158 |
| httpx_2278 | 2/2 (of 3) | 10/13 | $0.26 | 177 |
| httpx_2423 | 1/3 (of 5) | 4/7 | $0.22 | 130 |
| httpx_3139 | 1/1 (of 3) | 9/13 | $0.34 | 117 |
| httpx_887 | 2/2 (of 3) | 5/8 | $0.12 | 78 |
| requests_2523 | 1/2 (of 3) | 8/13 | $0.23 | 124 |
| requests_3366 | 1/2 (of 4) | 11/14 | $0.24 | 139 |
| requests_3655 | 1/3 (of 5) | 11/16 | $0.29 | 145 |
| requests_3984 | 1/1 (of 4) | 4/11 | $0.25 | 110 |
| requests_4718 | 1/4 (of 4) | 4/5 | $0.17 | 73 |
| requests_5856 | 2/3 (of 4) | 11/15 | $0.35 | 232 |


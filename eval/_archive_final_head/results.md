# Eval results

Matching of system findings to human review comments is manual judgment, documented per case in `eval/judgments/` (SPEC §5.2). Precision counts `uncertain` verdicts against the system.

Recall is reported over **matchable** ground truth: dataset SHAs are the final pre-merge heads, where most reviewer-raised issues were already fixed by the author during review and are therefore unfindable by any reviewer of that code. Each item's status is recorded per case as `matchable:` in the judgment files. Raw recall over all ground truth is shown for completeness.

## Headline

| experiment | cases | recall (matchable GT) | raw recall | precision proxy | valid/judged | mean $/PR | mean s/PR |
|---|---|---|---|---|---|---|---|
| full | 15 | 50% (7/14) | 13% (7/52) | 59% | 116/197 | $0.51 | 169 |
| baseline | 15 | 43% (6/14) | 12% (6/52) | 53% | 58/110 | $0.23 | 52 |
| no-tools | 15 | 43% (6/14) | 12% (6/52) | 56% | 100/179 | $0.26 | 137 |

## Ablation A — multi-agent vs monolithic baseline

- Recall (matchable GT): 50% vs 43% (7% delta)
- Precision: 59% vs 53%
- Cost: $0.51 vs $0.23 per PR

## Ablation B — tool loops on vs off

- Recall (matchable GT): 50% vs 43% (7% delta)
- Precision: 59% vs 56%
- Cost: $0.51 vs $0.26 per PR

## Confidence calibration (full system)

| confidence | judged | valid | validity rate |
|---|---|---|---|
| 0.40-0.55 | 20 | 3 | 15% |
| 0.55-0.70 | 28 | 9 | 32% |
| 0.70-0.85 | 73 | 37 | 51% |
| 0.85-1.00 | 76 | 67 | 88% |

## Per-case: full

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 13/14 | $0.34 | 141 |
| flask_2031 | 1/1 (of 2) | 12/14 | $0.36 | 141 |
| flask_2570 | 0/3 (of 3) | 13/18 | $1.22 | 234 |
| flask_3059 | 0/0 (of 2) | 5/9 | $0.51 | 134 |
| httpx_1197 | 1/2 (of 4) | 8/12 | $0.43 | 201 |
| httpx_2278 | 0/1 (of 3) | 8/14 | $0.39 | 171 |
| httpx_2423 | 1/1 (of 5) | 3/6 | $0.35 | 108 |
| httpx_3139 | 1/1 (of 3) | 7/16 | $0.94 | 193 |
| httpx_887 | 0/1 (of 3) | 3/9 | $0.28 | 235 |
| requests_2523 | 1/1 (of 3) | 7/13 | $0.49 | 150 |
| requests_3366 | 0/0 (of 4) | 11/16 | $0.44 | 145 |
| requests_3655 | 0/0 (of 5) | 9/18 | $0.67 | 226 |
| requests_3984 | 0/0 (of 4) | 2/9 | $0.42 | 150 |
| requests_4718 | 1/1 (of 4) | 4/12 | $0.33 | 135 |
| requests_5856 | 1/2 (of 4) | 11/17 | $0.54 | 167 |

## Per-case: baseline

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 6/9 | $0.22 | 79 |
| flask_2031 | 0/1 (of 2) | 5/9 | $0.14 | 40 |
| flask_2570 | 0/3 (of 3) | 0/0 | $0.52 | 14 |
| flask_3059 | 0/0 (of 2) | 4/7 | $0.25 | 41 |
| httpx_1197 | 2/2 (of 4) | 6/10 | $0.15 | 47 |
| httpx_2278 | 0/1 (of 3) | 3/8 | $0.18 | 54 |
| httpx_2423 | 1/1 (of 5) | 1/4 | $0.19 | 84 |
| httpx_3139 | 1/1 (of 3) | 5/11 | $0.40 | 51 |
| httpx_887 | 0/1 (of 3) | 1/7 | $0.13 | 54 |
| requests_2523 | 1/1 (of 3) | 5/8 | $0.14 | 39 |
| requests_3366 | 0/0 (of 4) | 8/10 | $0.27 | 47 |
| requests_3655 | 0/0 (of 5) | 6/10 | $0.17 | 44 |
| requests_3984 | 0/0 (of 4) | 0/5 | $0.20 | 104 |
| requests_4718 | 1/1 (of 4) | 4/4 | $0.22 | 40 |
| requests_5856 | 0/2 (of 4) | 4/8 | $0.25 | 39 |

## Per-case: no-tools

| case | matched GT (matchable/total) | valid/judged findings | $ | s |
|---|---|---|---|---|
| flask_1671 | 0/0 (of 3) | 14/15 | $0.23 | 129 |
| flask_2031 | 0/1 (of 2) | 12/13 | $0.20 | 114 |
| flask_2570 | 0/3 (of 3) | 8/17 | $0.36 | 188 |
| flask_3059 | 0/0 (of 2) | 7/11 | $0.22 | 111 |
| httpx_1197 | 2/2 (of 4) | 8/13 | $0.30 | 190 |
| httpx_2278 | 0/1 (of 3) | 3/9 | $0.23 | 164 |
| httpx_2423 | 1/1 (of 5) | 4/8 | $0.18 | 95 |
| httpx_3139 | 1/1 (of 3) | 7/15 | $0.36 | 118 |
| httpx_887 | 0/1 (of 3) | 1/7 | $0.13 | 88 |
| requests_2523 | 0/1 (of 3) | 4/10 | $0.23 | 132 |
| requests_3366 | 0/0 (of 4) | 10/15 | $0.25 | 144 |
| requests_3655 | 0/0 (of 5) | 8/14 | $0.39 | 225 |
| requests_3984 | 0/0 (of 4) | 2/8 | $0.27 | 123 |
| requests_4718 | 1/1 (of 4) | 3/10 | $0.23 | 105 |
| requests_5856 | 1/2 (of 4) | 9/14 | $0.28 | 124 |


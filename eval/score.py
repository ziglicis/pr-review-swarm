"""Scoring: completed judgment worksheets + cached results -> eval/results.md.

Pure functions over dicts so tests never touch the filesystem; file discovery
lives in collect()/write_report(). Invoked via run_eval.py --report.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).parent
EXPERIMENTS = ("full", "baseline", "no-tools")
CALIBRATION_BUCKETS = ((0.4, 0.55), (0.55, 0.7), (0.7, 0.85), (0.85, 1.01))


def is_complete(judgment: dict) -> bool:
    return all(m["matched_finding"] is not None or m.get("note") for m in
               judgment["ground_truth_matches"]) and all(
        v["valid"] is not None for v in judgment["finding_validity"])


def score_case(result: dict, judgment: dict) -> dict:
    """One case's counts. A ground-truth issue counts as matched when its
    matched_finding points at a finding index; validity is per system finding.

    Recall is computed over *matchable* ground truth only: the dataset pins each
    case to the final pre-merge SHA, where most reviewer-raised issues were
    already fixed by the author. An item is matchable when the judgment marks it
    still present at the reviewed head (absent flag = matchable, for old files).
    """
    matches = judgment["ground_truth_matches"]
    validity = judgment["finding_validity"]
    return {
        "case": result["case"],
        "gt_total": len(matches),
        "gt_matchable": sum(1 for m in matches if m.get("matchable", True)),
        "gt_matched": sum(1 for m in matches if m["matched_finding"] is not None),
        "valid": sum(1 for v in validity if v["valid"] is True),
        "invalid": sum(1 for v in validity if v["valid"] is False),
        "uncertain": sum(1 for v in validity if v["valid"] == "uncertain"),
        "findings": len(validity),
        "cost_usd": result["totals"]["cost_usd"],
        "duration_s": result["totals"]["duration_s"],
    }


def aggregate(rows: list[dict]) -> dict:
    gt_total = sum(r["gt_total"] for r in rows)
    gt_matchable = sum(r["gt_matchable"] for r in rows)
    gt_matched = sum(r["gt_matched"] for r in rows)
    judged = sum(r["findings"] for r in rows)
    valid = sum(r["valid"] for r in rows)
    return {
        "cases": len(rows),
        "gt_total": gt_total,
        "gt_matchable": gt_matchable,
        "gt_matched": gt_matched,
        "recall": gt_matched / gt_matchable if gt_matchable else 0.0,
        "recall_raw": gt_matched / gt_total if gt_total else 0.0,
        "findings": judged,
        "valid": valid,
        "uncertain": sum(r["uncertain"] for r in rows),
        # strict precision proxy: uncertain counts against
        "precision": valid / judged if judged else 0.0,
        "mean_cost_usd": sum(r["cost_usd"] for r in rows) / len(rows) if rows else 0.0,
        "mean_duration_s": sum(r["duration_s"] for r in rows) / len(rows) if rows else 0.0,
    }


def calibration_rows(results: list[dict], judgments: list[dict]) -> list[dict]:
    """Validity rate per confidence bucket (full system's own findings)."""
    pairs = []  # (confidence, valid: bool|"uncertain")
    for result, judgment in zip(results, judgments):
        for v in judgment["finding_validity"]:
            confidence = result["findings"][v["finding"]]["confidence"]
            pairs.append((confidence, v["valid"]))
    rows = []
    for lo, hi in CALIBRATION_BUCKETS:
        bucket = [(c, val) for c, val in pairs if lo <= c < hi]
        judged = len(bucket)
        valid = sum(1 for _, val in bucket if val is True)
        rows.append({
            "bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}",
            "n": judged,
            "valid": valid,
            "validity_rate": valid / judged if judged else None,
        })
    return rows


def collect(experiment: str) -> tuple[list[dict], list[dict], list[str]]:
    """(results, judgments, incomplete_case_names) for one experiment."""
    results, judgments, incomplete = [], [], []
    for path in sorted((EVAL_DIR / "results" / experiment).glob("*.json")):
        result = json.loads(path.read_text())
        jpath = EVAL_DIR / "judgments" / experiment / f"{path.stem}.yaml"
        if not jpath.exists():
            incomplete.append(path.stem)
            continue
        judgment = yaml.safe_load(jpath.read_text())
        if not is_complete(judgment):
            incomplete.append(path.stem)
            continue
        results.append(result)
        judgments.append(judgment)
    return results, judgments, incomplete


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def render_report(scores: dict[str, dict], calib: list[dict],
                  per_case: dict[str, list[dict]], notes: dict[str, list[str]]) -> str:
    out = ["# Eval results", "",
           "Matching of system findings to human review comments is manual judgment, "
           "documented per case in `eval/judgments/` (SPEC §5.2). Precision counts "
           "`uncertain` verdicts against the system.", "",
           "Recall is reported over **matchable** ground truth: dataset SHAs are the "
           "final pre-merge heads, where most reviewer-raised issues were already "
           "fixed by the author during review and are therefore unfindable by any "
           "reviewer of that code. Each item's status is recorded per case as "
           "`matchable:` in the judgment files. Raw recall over all ground truth is "
           "shown for completeness.", ""]

    out += ["## Headline", "",
            "| experiment | cases | recall (matchable GT) | raw recall | "
            "precision proxy | valid/judged | mean $/PR | mean s/PR |",
            "|---|---|---|---|---|---|---|---|"]
    for name, s in scores.items():
        out.append(
            f"| {name} | {s['cases']} | {_pct(s['recall'])} "
            f"({s['gt_matched']}/{s['gt_matchable']}) | {_pct(s['recall_raw'])} "
            f"({s['gt_matched']}/{s['gt_total']}) | {_pct(s['precision'])} | "
            f"{s['valid']}/{s['findings']} | ${s['mean_cost_usd']:.2f} | "
            f"{s['mean_duration_s']:.0f} |")
    out.append("")

    if "full" in scores and "baseline" in scores:
        f, b = scores["full"], scores["baseline"]
        out += ["## Ablation A — multi-agent vs monolithic baseline", "",
                f"- Recall (matchable GT): {_pct(f['recall'])} vs {_pct(b['recall'])} "
                f"({_pct(f['recall'] - b['recall'])} delta)",
                f"- Precision: {_pct(f['precision'])} vs {_pct(b['precision'])}",
                f"- Cost: ${f['mean_cost_usd']:.2f} vs ${b['mean_cost_usd']:.2f} per PR", ""]
    if "full" in scores and "no-tools" in scores:
        f, n = scores["full"], scores["no-tools"]
        out += ["## Ablation B — tool loops on vs off", "",
                f"- Recall (matchable GT): {_pct(f['recall'])} vs {_pct(n['recall'])} "
                f"({_pct(f['recall'] - n['recall'])} delta)",
                f"- Precision: {_pct(f['precision'])} vs {_pct(n['precision'])}",
                f"- Cost: ${f['mean_cost_usd']:.2f} vs ${n['mean_cost_usd']:.2f} per PR", ""]

    out += ["## Confidence calibration (full system)", "",
            "| confidence | judged | valid | validity rate |", "|---|---|---|---|"]
    for row in calib:
        rate = _pct(row["validity_rate"]) if row["validity_rate"] is not None else "—"
        out.append(f"| {row['bucket']} | {row['n']} | {row['valid']} | {rate} |")
    out.append("")

    for name, rows in per_case.items():
        out += [f"## Per-case: {name}", "",
                "| case | matched GT (matchable/total) | valid/judged findings | $ | s |",
                "|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['case']} | {r['gt_matched']}/{r['gt_matchable']} "
                       f"(of {r['gt_total']}) | "
                       f"{r['valid']}/{r['findings']} | ${r['cost_usd']:.2f} | "
                       f"{r['duration_s']:.0f} |")
        out.append("")

    for name, missing in notes.items():
        if missing:
            out.append(f"_{name}: {len(missing)} case(s) excluded pending judgment: "
                       f"{', '.join(missing)}_")
    return "\n".join(out) + "\n"


def write_report() -> Path:
    scores, per_case, notes = {}, {}, {}
    calib: list[dict] = []
    for experiment in EXPERIMENTS:
        results, judgments, incomplete = collect(experiment)
        notes[experiment] = incomplete
        if not results:
            continue
        rows = [score_case(r, j) for r, j in zip(results, judgments)]
        scores[experiment] = aggregate(rows)
        per_case[experiment] = rows
        if experiment == "full":
            calib = calibration_rows(results, judgments)
    path = EVAL_DIR / "results.md"
    path.write_text(render_report(scores, calib, per_case, notes))
    return path

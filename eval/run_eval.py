"""Eval runner: execute one experiment over the ground-truth dataset.

Usage:
    uv run python -m eval.run_eval --experiment full [--case requests_5856] [--force]

Results are cached per case under eval/results/{experiment}/ — a rerun spends
nothing. A judgment worksheet is generated per result for the manual matching
step (SPEC §5.2); scoring reads the completed worksheets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

if __package__ is None:  # ran as a bare script instead of -m
    sys.path.insert(0, str(Path(__file__).parent.parent))

from eval import baseline_agent  # noqa: E402
from src import orchestrator, synthesizer  # noqa: E402
from src.agents.test_coverage import looks_like_test  # noqa: E402
from src.cli import load_dotenv  # noqa: E402
from src.diff_parser import parse_diff  # noqa: E402
from src.github_client import GitHubClient  # noqa: E402
from src.models import AgentRun, Finding  # noqa: E402
from src.tools import ToolExecutor  # noqa: E402

EVAL_DIR = Path(__file__).parent
EXPERIMENTS = ("full", "baseline", "no-tools")


def load_dataset(case_filter: str | None = None) -> list[dict]:
    cases = []
    for path in sorted((EVAL_DIR / "dataset").glob("*.yaml")):
        if case_filter and case_filter not in path.stem:
            continue
        case = yaml.safe_load(path.read_text())
        case["case"] = path.stem
        cases.append(case)
    return cases


def result_path(experiment: str, case_name: str) -> Path:
    return EVAL_DIR / "results" / experiment / f"{case_name}.json"


def worksheet_path(experiment: str, case_name: str) -> Path:
    return EVAL_DIR / "judgments" / experiment / f"{case_name}.yaml"


def result_to_dict(
    case: dict,
    experiment: str,
    findings: list[Finding],
    agent_runs: list[AgentRun],
    summary: str,
    head_sha: str,
) -> dict:
    return {
        "case": case["case"],
        "experiment": experiment,
        "pr_url": case["url"],
        "head_sha": head_sha,
        "sha_matches_dataset": head_sha == case["pre_merge_sha"],
        "timestamp": int(time.time()),
        "summary": summary,
        "findings": [
            {
                "agent": f.agent,
                "file": f.file,
                "line_start": f.line_range[0],
                "line_end": f.line_range[1],
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "confidence": f.confidence,
            }
            for f in findings
        ],
        "agent_runs": [asdict(r) for r in agent_runs],
        "totals": {
            "tokens_in": sum(r.tokens_in for r in agent_runs),
            "tokens_out": sum(r.tokens_out for r in agent_runs),
            "cost_usd": round(sum(r.cost_usd for r in agent_runs), 6),
            "duration_s": round(sum(r.duration_s for r in agent_runs), 1),
        },
    }


def build_worksheet(case: dict, result: dict) -> dict:
    """Empty verdict scaffold: one match slot per ground-truth issue, one
    validity slot per system finding. Filled by manual judgment."""
    return {
        "case": case["case"],
        "experiment": result["experiment"],
        "ground_truth_matches": [
            {
                "ground_truth": g["id"],
                "issue": g["issue"].strip(),
                "matched_finding": None,  # index into result findings, or null
                "note": "",
            }
            for g in case["ground_truth"]
        ],
        "finding_validity": [
            {
                "finding": i,
                "title": f["title"],
                "valid": None,  # true | false | "uncertain"
                "note": "",
            }
            for i, f in enumerate(result["findings"])
        ],
    }


async def run_case(case: dict, experiment: str) -> dict:
    gh = GitHubClient()
    try:
        pr = await gh.fetch_pr(case["url"])
        files = parse_diff(pr.diff)
        if experiment == "baseline":
            file_contents = await orchestrator.fetch_file_contents(gh, pr, files)
            try:
                tree = await gh.get_tree(pr.owner, pr.repo, pr.head_sha)
                test_paths = [p for p in tree if looks_like_test(p)][:500]
            except Exception:
                test_paths = []
            run = await baseline_agent.run(
                pr, files, file_contents, test_paths, ToolExecutor(gh, pr)
            )
            findings, agent_runs, summary = run.findings, [run], ""
        else:
            runs = await orchestrator.run_review(
                pr, files, gh, no_tools=(experiment == "no-tools")
            )
            review = await synthesizer.synthesize(pr, runs, {f.path for f in files})
            findings, agent_runs, summary = review.findings, review.agent_runs, review.summary
        return result_to_dict(case, experiment, findings, agent_runs, summary, pr.head_sha)
    finally:
        await gh.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=EXPERIMENTS, required=True)
    parser.add_argument("--case", help="substring filter on case names")
    parser.add_argument("--force", action="store_true", help="rerun cached cases")
    args = parser.parse_args()
    load_dotenv(EVAL_DIR.parent / ".env")

    cases = load_dataset(args.case)
    if not cases:
        print("no dataset cases matched", file=sys.stderr)
        return 1

    total_cost = 0.0
    for case in cases:
        out = result_path(args.experiment, case["case"])
        if out.exists() and not args.force:
            print(f"[cached] {case['case']}")
            result = json.loads(out.read_text())
        else:
            print(f"[run]    {case['case']} …", flush=True)
            result = await run_case(case, args.experiment)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2))
            if not result["sha_matches_dataset"]:
                print("  WARNING: head sha differs from dataset pre_merge_sha", file=sys.stderr)
            print(f"  {len(result['findings'])} findings · "
                  f"${result['totals']['cost_usd']:.4f} · {result['totals']['duration_s']}s")
        total_cost += result["totals"]["cost_usd"]

        ws = worksheet_path(args.experiment, case["case"])
        if not ws.exists():
            ws.parent.mkdir(parents=True, exist_ok=True)
            ws.write_text(yaml.safe_dump(build_worksheet(case, result), sort_keys=False,
                                         allow_unicode=True, width=88))
    print(f"\n{args.experiment}: {len(cases)} case(s), total cost ${total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

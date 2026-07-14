"""CLI entry point: python -m src.cli review <pr-url>"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

from src import orchestrator
from src.diff_parser import parse_diff
from src.github_client import GitHubClient, PRData, PRTooLargeError
from src.models import AgentRun

SEVERITY_ORDER = ("critical", "major", "minor", "nit")


async def review(url: str) -> tuple[PRData, list[AgentRun]]:
    gh = GitHubClient()
    try:
        pr = await gh.fetch_pr(url)
        files = parse_diff(pr.diff)
        runs = await orchestrator.run_review(pr, files, gh)
        return pr, runs
    finally:
        await gh.aclose()


def _format_agent_line(run: AgentRun) -> str:
    if run.status == "skipped":
        return f"- {run.agent}: skipped — {run.skip_or_error_reason}"
    if run.status != "ok":
        return f"- {run.agent}: {run.status} — {run.skip_or_error_reason}"
    return (
        f"- {run.agent}: {len(run.findings)} finding(s) · {run.tool_calls} tool call(s) · "
        f"{run.tokens_in} in / {run.tokens_out} out · ${run.cost_usd:.4f} · "
        f"{run.duration_s:.1f}s"
    )


def format_review(pr: PRData, runs: list[AgentRun]) -> str:
    out = [f"# Review: {pr.title}",
           f"https://github.com/{pr.owner}/{pr.repo}/pull/{pr.number}", ""]

    findings = sorted(
        (f for r in runs if r.status == "ok" for f in r.findings),
        key=lambda f: (SEVERITY_ORDER.index(f.severity), -f.confidence),
    )
    if not findings:
        out.append("No issues found.")
    for severity in SEVERITY_ORDER:
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        out.append(f"## {severity.capitalize()}")
        for f in group:
            lo, hi = f.line_range
            loc = f"{f.file}:{lo}" if lo == hi else f"{f.file}:{lo}-{hi}"
            out.append(f"- **{loc}** — {f.title} _({f.agent}, confidence {f.confidence:.1f})_")
            out.append(f"  {f.detail}")
        out.append("")

    out.append("## Agent runs")
    out.extend(_format_agent_line(r) for r in runs)
    total_in = sum(r.tokens_in for r in runs)
    total_out = sum(r.tokens_out for r in runs)
    total_cost = sum(r.cost_usd for r in runs)
    wall = max((r.duration_s for r in runs), default=0.0)  # agents run in parallel
    out.append("")
    out.append(
        f"_total: {total_in} in / {total_out} out tokens · ${total_cost:.4f} · "
        f"{wall:.1f}s wall_"
    )
    return "\n".join(out)


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader; values override the inherited environment on purpose,
    so a project-local key beats a stale one in the shell profile."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("'\"")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="pr-review-swarm")
    sub = parser.add_subparsers(dest="command", required=True)
    review_cmd = sub.add_parser("review", help="Review a public GitHub PR")
    review_cmd.add_argument("pr_url", help="e.g. https://github.com/owner/repo/pull/123")
    args = parser.parse_args(argv)

    try:
        pr, runs = asyncio.run(review(args.pr_url))
    except (ValueError, PRTooLargeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(f"error: GitHub API returned {e.response.status_code} — is the PR URL correct "
              "and the repo public?", file=sys.stderr)
        return 1

    print(format_review(pr, runs))
    executed = [r for r in runs if r.status != "skipped"]
    # all-skipped (e.g. docs-only PR) is a successful no-op, not a failure
    return 0 if not executed or any(r.status == "ok" for r in executed) else 2


if __name__ == "__main__":
    sys.exit(main())

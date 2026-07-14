"""CLI entry point: python -m src.cli review <pr-url>"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

from src import orchestrator, synthesizer
from src.diff_parser import parse_diff
from src.github_client import GitHubClient, PRData, PRTooLargeError
from src.models import AgentRun, Review
from src.trace import Tracer

SEVERITY_ORDER = ("critical", "major", "minor", "nit")


async def review(url: str) -> tuple[PRData, Review, Tracer]:
    gh = GitHubClient()
    tracer = Tracer()
    try:
        pr = await gh.fetch_pr(url)
        files = parse_diff(pr.diff)
        runs = await orchestrator.run_review(pr, files, gh, tracer=tracer)
        result = await synthesizer.synthesize(
            pr, runs, {f.path for f in files}, tracer=tracer
        )
        return pr, result, tracer
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


def format_review(pr: PRData, result: Review) -> str:
    out = [f"# Review: {pr.title}", result.pr_url, "", result.summary, ""]

    for severity in SEVERITY_ORDER:
        group = [f for f in result.findings if f.severity == severity]
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
    out.extend(_format_agent_line(r) for r in result.agent_runs)
    out.append("")
    out.append(
        f"_total: {result.stats.total_tokens} tokens · ${result.stats.total_cost_usd:.4f} · "
        f"{result.stats.duration_s:.1f}s_"
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
        pr, result, tracer = asyncio.run(review(args.pr_url))
    except (ValueError, PRTooLargeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(f"error: GitHub API returned {e.response.status_code} — is the PR URL correct "
              "and the repo public?", file=sys.stderr)
        return 1

    print(format_review(pr, result))
    trace_path = tracer.write(
        Path("traces") / f"{pr.owner}_{pr.repo}_{pr.number}_{int(time.time())}.json"
    )
    print(f"trace: {trace_path}", file=sys.stderr)  # stderr keeps stdout pure markdown
    executed = [
        r for r in result.agent_runs if r.status != "skipped" and r.agent != "synthesizer"
    ]
    # all-skipped (e.g. docs-only PR) is a successful no-op, not a failure
    return 0 if not executed or any(r.status == "ok" for r in executed) else 2


if __name__ == "__main__":
    sys.exit(main())

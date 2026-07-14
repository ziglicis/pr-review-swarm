"""CLI entry point: python -m src.cli review <pr-url>"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

from src.agents import correctness
from src.diff_parser import parse_diff
from src.github_client import GitHubClient, PRData, PRTooLargeError
from src.models import AgentRun
from src.tools import ToolExecutor

SEVERITY_ORDER = ("critical", "major", "minor", "nit")


async def review(url: str) -> tuple[PRData, AgentRun]:
    gh = GitHubClient()
    try:
        pr = await gh.fetch_pr(url)
        files = parse_diff(pr.diff)
        contents: dict[str, str] = {}
        for fd in files:
            if fd.is_binary or fd.status == "deleted" or not fd.hunks:
                continue
            try:
                contents[fd.path] = await gh.get_file(pr.owner, pr.repo, fd.path, pr.head_sha)
            except httpx.HTTPError:
                pass  # context builder falls back to bare hunks for this file
        run = await correctness.run(pr, files, contents, executor=ToolExecutor(gh, pr))
        return pr, run
    finally:
        await gh.aclose()


def format_review(pr: PRData, run: AgentRun) -> str:
    out = [f"# Review: {pr.title}", f"{pr.diff.count(chr(10))} diff lines · "
           f"https://github.com/{pr.owner}/{pr.repo}/pull/{pr.number}", ""]
    if run.status != "ok":
        out.append(f"**Correctness review unavailable** ({run.status}): {run.skip_or_error_reason}")
    elif not run.findings:
        out.append("No correctness issues found.")
    else:
        for severity in SEVERITY_ORDER:
            group = [f for f in run.findings if f.severity == severity]
            if not group:
                continue
            out.append(f"## {severity.capitalize()}")
            for f in group:
                lo, hi = f.line_range
                loc = f"{f.file}:{lo}" if lo == hi else f"{f.file}:{lo}-{hi}"
                out.append(f"- **{loc}** — {f.title} _(confidence {f.confidence:.1f})_")
                out.append(f"  {f.detail}")
            out.append("")
    out.append("---")
    out.append(
        f"_{run.agent}: {len(run.findings)} finding(s) · "
        f"{run.tokens_in} in / {run.tokens_out} out tokens · "
        f"${run.cost_usd:.4f} · {run.duration_s:.1f}s_"
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
        pr, run = asyncio.run(review(args.pr_url))
    except (ValueError, PRTooLargeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(f"error: GitHub API returned {e.response.status_code} — is the PR URL correct "
              "and the repo public?", file=sys.stderr)
        return 1

    print(format_review(pr, run))
    return 0 if run.status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())

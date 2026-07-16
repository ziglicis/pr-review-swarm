"""Ablation A: one monolithic generalist reviewer.

Same model, same tools and budgets, same report_findings schema, same confidence
filter as the full system — the only variables removed are the specialist split
and the synthesis step. The recall/precision delta against the full system is
the justification (or indictment) of the multi-agent design.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from src.agents import correctness
from src.agents.base import run_with_tools
from src.diff_parser import FileDiff
from src.github_client import PRData
from src.models import AgentRun
from src.synthesizer import CONFIDENCE_THRESHOLD, SEVERITY_RANK
from src.tools import (
    EXPAND_CONTEXT_TOOL,
    LIST_FILES_TOOL,
    READ_FILE_TOOL,
    ToolExecutor,
)

SYSTEM = """\
You are a senior engineer performing a COMPLETE code review of a pull request,
covering all of the following:

- CORRECTNESS: logic errors, off-by-one bugs, unhandled edge cases, race
  conditions, incorrect API usage, broken invariants.
- SECURITY: injection risks, hardcoded secrets, unsafe deserialization, path
  traversal, missing auth checks, risky dependency/CI/Docker changes.
- STYLE: unclear naming, dead code, needless complexity, deviation from the
  conventions visible in the surrounding code. Style findings are never
  blocking: use only severity "minor" or "nit" for them.
- TEST COVERAGE: new or changed logic without corresponding test changes,
  weakened or deleted tests, untested branches.

The code is annotated with real file line numbers (new-file side); cite them.
Lines starting with '+' are added, '-' removed, others context.

You may use read_file, expand_context, and list_files (a few calls at most) to
check code outside the diff before claiming an issue. Report every issue you
find, including uncertain ones; set confidence (0.0-1.0) to reflect certainty.
When done, call report_findings.
"""


def build_context(
    pr: PRData,
    files: list[FileDiff],
    file_contents: dict[str, str],
    repo_test_paths: list[str],
) -> str:
    """Union of the specialists' inputs: expanded hunks + file listing + test names."""
    parts = [correctness.build_context(pr, files, file_contents)]
    parts.append("\n## All files changed in this PR")
    parts.extend(
        f"- {fd.path} ({fd.status}{', binary' if fd.is_binary else ''})" for fd in files
    )
    parts.append("\n## Test files in the repository (names only)")
    if repo_test_paths:
        parts.extend(f"- {p}" for p in repo_test_paths)
    else:
        parts.append("(none)")
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    file_contents: dict[str, str],
    repo_test_paths: list[str],
    executor: ToolExecutor,
    client: AsyncAnthropic | None = None,
) -> AgentRun:
    result = await run_with_tools(
        agent_name="baseline",
        system=SYSTEM,
        user_content=build_context(pr, files, file_contents, repo_test_paths),
        valid_files={f.path for f in files},
        executor=executor,
        investigation_tools=[EXPAND_CONTEXT_TOOL, READ_FILE_TOOL, LIST_FILES_TOOL],
        client=client,
    )
    # same post-filter + ordering the full system applies after synthesis
    result.findings = sorted(
        (f for f in result.findings if f.confidence >= CONFIDENCE_THRESHOLD),
        key=lambda f: (SEVERITY_RANK[f.severity], -f.confidence),
    )
    return result

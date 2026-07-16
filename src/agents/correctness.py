"""Correctness agent: bounded investigation loop (max 3 tool calls)."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from src.agents.base import run_with_tools
from src.diff_parser import FileDiff, Hunk
from src.github_client import PRData
from src.models import AgentRun
from src.tools import EXPAND_CONTEXT_TOOL, READ_FILE_TOOL, ToolExecutor, numbered
from src.trace import Tracer

CONTEXT_LINES = 30  # surrounding-function context around each hunk

SYSTEM = """\
You are a senior engineer reviewing a pull request strictly for CORRECTNESS.

Look for: logic errors, off-by-one bugs, unhandled edge cases, race conditions,
incorrect API usage, and broken invariants introduced by this change.

Do NOT report style, naming, formatting, test coverage, or security concerns —
other reviewers own those. Only report issues in the changed code or directly
caused by it.

The code you are given is annotated with real file line numbers (new-file side).
Cite those numbers. Lines starting with '+' are added, '-' removed, others context.

Report every correctness issue you find, including ones you are uncertain about —
set confidence (0.0-1.0) to reflect your certainty; a downstream filter ranks them.

You may use read_file and expand_context (a few calls at most) to check code outside
the diff before claiming a bug (e.g. how a modified function is called elsewhere).
When you have enough evidence, call report_findings.
"""


def build_context(pr: PRData, files: list[FileDiff], file_contents: dict[str, str]) -> str:
    """Diff hunks expanded to ±30 lines of surrounding file content at head SHA."""
    parts = [f"# PR: {pr.title}\n\n{pr.body or '(no description)'}\n"]
    for fd in files:
        if fd.is_binary or not fd.hunks:
            continue
        parts.append(f"\n## {fd.path} ({fd.status})")
        lines = file_contents.get(fd.path, "").splitlines()
        for hunk in fd.hunks:
            parts.append(_expand_hunk(hunk, lines))
    return "\n".join(parts)


def _expand_hunk(hunk: Hunk, lines: list[str]) -> str:
    parts = []
    before_start = max(1, hunk.new_start - CONTEXT_LINES)
    if lines and before_start < hunk.new_start:
        parts.append(numbered(lines, before_start, hunk.new_start - 1))
    parts.append(hunk.annotated())
    after_start = hunk.new_start + hunk.new_count
    after_end = min(len(lines), after_start + CONTEXT_LINES - 1)
    if lines and after_start <= after_end:
        parts.append(numbered(lines, after_start, after_end))
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    file_contents: dict[str, str],
    executor: ToolExecutor,
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
    use_tools: bool = True,  # False = single-pass (eval Ablation B)
) -> AgentRun:
    return await run_with_tools(
        agent_name="correctness",
        system=SYSTEM,
        user_content=build_context(pr, files, file_contents),
        valid_files={f.path for f in files},
        executor=executor,
        investigation_tools=[EXPAND_CONTEXT_TOOL, READ_FILE_TOOL] if use_tools else [],
        client=client,
        tracer=tracer,
    )

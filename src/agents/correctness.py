"""Correctness agent: single-pass in Phase 1, tool loop arrives in Phase 2."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from src.agents.base import run_single_pass
from src.diff_parser import FileDiff, Hunk
from src.github_client import PRData
from src.models import AgentRun

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


def _numbered(lines: list[str], start: int, end: int) -> str:
    """1-based inclusive slice of file lines, formatted like Hunk.annotated()."""
    return "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))


def _expand_hunk(hunk: Hunk, lines: list[str]) -> str:
    parts = []
    before_start = max(1, hunk.new_start - CONTEXT_LINES)
    if lines and before_start < hunk.new_start:
        parts.append(_numbered(lines, before_start, hunk.new_start - 1))
    parts.append(hunk.annotated())
    after_start = hunk.new_start + hunk.new_count
    after_end = min(len(lines), after_start + CONTEXT_LINES - 1)
    if lines and after_start <= after_end:
        parts.append(_numbered(lines, after_start, after_end))
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    file_contents: dict[str, str],
    client: AsyncAnthropic | None = None,
) -> AgentRun:
    return await run_single_pass(
        agent_name="correctness",
        system=SYSTEM,
        user_content=build_context(pr, files, file_contents),
        valid_files={f.path for f in files},
        client=client,
    )

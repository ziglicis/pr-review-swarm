"""Style agent: single-pass, severity capped at minor/nit by design."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from src.agents.base import run_single_pass
from src.diff_parser import FileDiff
from src.github_client import PRData
from src.models import AgentRun
from src.trace import Tracer

SAMPLE_LINES = 80  # untouched-code sample per file, so style is judged in context

SYSTEM = """\
You are reviewing a pull request strictly for STYLE and readability.

Look for: unclear naming, dead code, needless complexity, and deviation from the
conventions visible in the surrounding code of the same files.

You are shown a sample of UNTOUCHED code from each changed file — judge consistency
with that existing style, not against generic preferences. If the existing code uses
a convention, the change should follow it, even if you would prefer another.

Do NOT report correctness bugs, security issues, or test coverage — other reviewers
own those. Style findings are never blocking: use ONLY severity "minor" or "nit".

The code is annotated with real file line numbers (new-file side); cite them.
Set confidence (0.0-1.0) to reflect certainty.
"""


def sample_untouched(fd: FileDiff, lines: list[str], limit: int = SAMPLE_LINES) -> str:
    """First `limit` file lines that are outside every hunk's new-side range."""
    covered = set()
    for h in fd.hunks:
        covered.update(range(h.new_start, h.new_start + h.new_count))
    sample = []
    for n in range(1, len(lines) + 1):
        if n not in covered:
            sample.append(f"{n:>5}  {lines[n - 1]}")
            if len(sample) >= limit:
                break
    return "\n".join(sample)


def build_context(pr: PRData, files: list[FileDiff], file_contents: dict[str, str]) -> str:
    parts = [f"# PR: {pr.title}\n\n{pr.body or '(no description)'}\n"]
    for fd in files:
        if fd.is_binary or not fd.hunks:
            continue
        parts.append(f"\n## {fd.path} ({fd.status}) — changed hunks")
        parts.extend(hunk.annotated() for hunk in fd.hunks)
        content = file_contents.get(fd.path)
        if content:
            parts.append(f"\n### {fd.path} — existing style sample (untouched lines)")
            parts.append(sample_untouched(fd, content.splitlines()))
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    file_contents: dict[str, str],
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
) -> AgentRun:
    result = await run_single_pass(
        agent_name="style",
        system=SYSTEM,
        user_content=build_context(pr, files, file_contents),
        valid_files={f.path for f in files},
        client=client,
        tracer=tracer,
    )
    for f in result.findings:  # capped by design; prompt asks, clamp guarantees
        if f.severity in ("critical", "major"):
            f.severity = "minor"
    return result

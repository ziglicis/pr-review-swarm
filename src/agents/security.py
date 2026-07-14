"""Security agent: bounded investigation loop (read_file only)."""

from __future__ import annotations

from pathlib import PurePosixPath

from anthropic import AsyncAnthropic

from src.agents.base import run_with_tools
from src.diff_parser import FileDiff
from src.github_client import PRData
from src.models import AgentRun
from src.tools import READ_FILE_TOOL, ToolExecutor
from src.trace import Tracer

_DOC_EXTS = {".md", ".rst", ".txt"}

SYSTEM = """\
You are a security engineer reviewing a pull request strictly for SECURITY issues.

Look for: injection risks (SQL, shell, template), hardcoded secrets or credentials,
unsafe deserialization, path traversal, missing auth/permission checks, and risky
dependency or CI/Docker changes introduced by this change.

Do NOT report correctness bugs, style, or test coverage — other reviewers own those.
The full list of files this PR touches is included: flag suspicious additions
(e.g. new .env files, unexpected binaries, CI changes that leak secrets).

The code is annotated with real file line numbers (new-file side); cite them.
Lines starting with '+' are added, '-' removed, others context.

You may use read_file (a few calls at most) to check context outside the diff —
e.g. whether user input flagged in a hunk is validated upstream — before claiming
an issue. Report every issue you find, including uncertain ones; set confidence
(0.0-1.0) to reflect certainty. When done, call report_findings.
"""


def _is_doc(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _DOC_EXTS


def build_context(pr: PRData, files: list[FileDiff]) -> str:
    parts = [f"# PR: {pr.title}\n\n{pr.body or '(no description)'}\n"]
    parts.append("## All files changed in this PR")
    for fd in files:
        parts.append(f"- {fd.path} ({fd.status}{', binary' if fd.is_binary else ''})")
    for fd in files:
        if fd.is_binary or _is_doc(fd.path) or not fd.hunks:
            continue
        parts.append(f"\n## {fd.path} ({fd.status})")
        parts.extend(hunk.annotated() for hunk in fd.hunks)
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    executor: ToolExecutor,
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
) -> AgentRun:
    return await run_with_tools(
        agent_name="security",
        system=SYSTEM,
        user_content=build_context(pr, files),
        valid_files={f.path for f in files},
        executor=executor,
        investigation_tools=[READ_FILE_TOOL],
        client=client,
        tracer=tracer,
    )

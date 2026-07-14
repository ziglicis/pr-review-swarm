"""Test Coverage agent: single-pass."""

from __future__ import annotations

from pathlib import PurePosixPath

from anthropic import AsyncAnthropic

from src.agents.base import run_single_pass
from src.diff_parser import FileDiff
from src.github_client import PRData
from src.models import AgentRun
from src.trace import Tracer

SYSTEM = """\
You are reviewing a pull request strictly for TEST COVERAGE.

Look for: new or changed logic with no corresponding test changes; weakened or
deleted tests; new branches or edge cases with no test exercising them.

Do NOT report correctness bugs, style, or security — other reviewers own those.
Point findings at the SOURCE lines that lack coverage (or the test lines that were
weakened), using the annotated line numbers. You are given the repo's test file
listing and the full contents of any test files this PR changed — use them to judge
what is and isn't covered.

Report every gap you find, including uncertain ones; set confidence (0.0-1.0) to
reflect certainty.
"""


def looks_like_test(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = {part.lower() for part in p.parts}
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
        or "tests" in parts
        or "test" in parts
        or "__tests__" in parts
    )


def build_context(
    pr: PRData,
    files: list[FileDiff],
    repo_test_paths: list[str],
    test_file_contents: dict[str, str],
) -> str:
    parts = [f"# PR: {pr.title}\n\n{pr.body or '(no description)'}\n"]
    for fd in files:
        if fd.is_binary or not fd.hunks:
            continue
        kind = "test file" if looks_like_test(fd.path) else "source file"
        parts.append(f"\n## {fd.path} ({fd.status}, {kind})")
        parts.extend(hunk.annotated() for hunk in fd.hunks)
    parts.append("\n## Test files in the repository (names only)")
    if repo_test_paths:
        parts.extend(f"- {p}" for p in repo_test_paths)
    else:
        parts.append("(none)")
    for path, content in test_file_contents.items():
        parts.append(f"\n## Full contents of changed test file: {path}")
        parts.append(content)
    return "\n".join(parts)


async def run(
    pr: PRData,
    files: list[FileDiff],
    repo_test_paths: list[str],
    test_file_contents: dict[str, str],
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
) -> AgentRun:
    return await run_single_pass(
        agent_name="test_coverage",
        system=SYSTEM,
        user_content=build_context(pr, files, repo_test_paths, test_file_contents),
        valid_files={f.path for f in files},
        client=client,
        tracer=tracer,
    )

"""Deterministic orchestrator: classify the PR, select agents, run them in parallel.

Rule-based on purpose — routing doesn't need judgment, and this way it's testable.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import PurePosixPath
from typing import Awaitable, Callable

import httpx
from anthropic import AsyncAnthropic

from src.agents import correctness, security, style, test_coverage
from src.agents.test_coverage import looks_like_test
from src.diff_parser import FileDiff
from src.github_client import GitHubClient, PRData
from src.models import AgentRun
from src.tools import ToolExecutor
from src.trace import Tracer

AGENT_TIMEOUT_S = 120
CONTEXT_BUDGET_CHARS = 80_000  # ~20k tokens at ~4 chars/token
AGENT_ORDER = ("correctness", "security", "style", "test_coverage")

_DOC_EXTS = {".md", ".rst", ".txt"}
_CONFIG_EXTS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".lock"}
_MANIFESTS = {
    "requirements.txt", "pyproject.toml", "setup.py", "package.json",
    "package-lock.json", "go.mod", "go.sum", "cargo.toml", "gemfile", "gemfile.lock",
}


def file_kind(path: str) -> str:
    """"doc" | "config" | "code" from extension and path."""
    p = PurePosixPath(path)
    first = p.parts[0].lower() if p.parts else ""
    if p.suffix.lower() in _DOC_EXTS or first in ("docs", "doc"):
        return "doc"
    if p.suffix.lower() in _CONFIG_EXTS or p.name.startswith("."):
        return "config"
    return "code"


def classify(files: list[FileDiff]) -> str:
    kinds = {file_kind(f.path) for f in files}
    if kinds <= {"doc"}:
        return "docs-only"
    if kinds <= {"doc", "config"}:
        return "config-only"
    if kinds == {"code"}:
        return "code"
    return "mixed"


def security_relevant(files: list[FileDiff]) -> bool:
    """Source code, dependency manifests, CI config, or Dockerfiles."""
    for f in files:
        name = PurePosixPath(f.path).name.lower()
        if (
            file_kind(f.path) == "code"
            or name in _MANIFESTS
            or name.startswith("dockerfile")
            or "docker-compose" in name
            or ".github/workflows" in f.path
            or name in ("jenkinsfile", ".gitlab-ci.yml")
        ):
            return True
    return False


def select_agents(files: list[FileDiff]) -> dict[str, str | None]:
    """Agent name -> skip reason (None = run). Every skip carries its reason."""
    c = classify(files)
    selection: dict[str, str | None] = dict.fromkeys(AGENT_ORDER)
    any_code = any(file_kind(f.path) == "code" for f in files)
    if c in ("docs-only", "config-only"):
        selection["correctness"] = f"{c} change"
    if not security_relevant(files):
        selection["security"] = "no security-relevant files changed"
    if not any_code:
        selection["style"] = "no code files changed"
    if not any(file_kind(f.path) == "code" and not looks_like_test(f.path) for f in files):
        selection["test_coverage"] = "no non-test source files changed"
    return selection


def split_batches(
    files: list[FileDiff],
    size_fn: Callable[[FileDiff], int],
    budget: int = CONTEXT_BUDGET_CHARS,
) -> list[list[FileDiff]]:
    """Greedy pack files so each batch's estimated context stays under budget."""
    batches: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    current_size = 0
    for fd in files:
        size = size_fn(fd)
        if current and current_size + size > budget:
            batches.append(current)
            current, current_size = [], 0
        current.append(fd)
        current_size += size
    if current:
        batches.append(current)
    return batches


def merge_runs(runs: list[AgentRun]) -> AgentRun:
    """Merge per-batch runs into one AgentRun. Findings from ok batches are kept."""
    failed = [r for r in runs if r.status != "ok"]
    return AgentRun(
        agent=runs[0].agent,
        status="ok" if not failed else failed[0].status,
        findings=[f for r in runs if r.status == "ok" for f in r.findings],
        tool_calls=sum(r.tool_calls for r in runs),
        tokens_in=sum(r.tokens_in for r in runs),
        tokens_out=sum(r.tokens_out for r in runs),
        cost_usd=sum(r.cost_usd for r in runs),
        duration_s=sum(r.duration_s for r in runs),
        skip_or_error_reason=(
            None if not failed
            else f"{len(failed)}/{len(runs)} batches failed: {failed[0].skip_or_error_reason}"
        ),
    )


async def guarded(
    name: str,
    factory: Callable[[], Awaitable[AgentRun]],
    timeout: float = AGENT_TIMEOUT_S,
) -> AgentRun:
    """Per-agent timeout + failure isolation: one agent failing never fails the run."""
    start = time.monotonic()
    try:
        return await asyncio.wait_for(factory(), timeout=timeout)
    except TimeoutError:
        return AgentRun(
            agent=name, status="timeout", findings=[], tool_calls=0, tokens_in=0,
            tokens_out=0, cost_usd=0.0, duration_s=time.monotonic() - start,
            skip_or_error_reason=f"exceeded {timeout:.0f}s timeout",
        )
    except Exception as e:
        return AgentRun(
            agent=name, status="error", findings=[], tool_calls=0, tokens_in=0,
            tokens_out=0, cost_usd=0.0, duration_s=time.monotonic() - start,
            skip_or_error_reason=f"{type(e).__name__}: {e}",
        )


async def _batched(run_one, files, size_fn) -> AgentRun:
    batches = split_batches(files, size_fn)
    if len(batches) == 1:
        return await run_one(batches[0])
    return merge_runs([await run_one(batch) for batch in batches])


async def fetch_file_contents(
    gh: GitHubClient, pr: PRData, files: list[FileDiff]
) -> dict[str, str]:
    """Head-SHA contents for reviewable changed files; fetch failures are skipped."""
    contents: dict[str, str] = {}
    for fd in files:
        if fd.is_binary or fd.status == "deleted" or not fd.hunks:
            continue
        try:
            contents[fd.path] = await gh.get_file(pr.owner, pr.repo, fd.path, pr.head_sha)
        except httpx.HTTPError:
            pass  # context builders fall back to bare hunks
    return contents


async def run_review(
    pr: PRData,
    files: list[FileDiff],
    gh: GitHubClient,
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
    no_tools: bool = False,  # eval Ablation B: force all agents single-pass
) -> list[AgentRun]:
    """Select and run agents concurrently; returns one AgentRun per agent, in order."""
    tracer = tracer or Tracer()
    selection = select_agents(files)
    reviewable = [f for f in files if not f.is_binary and f.hunks]
    executor = ToolExecutor(gh, pr)
    tracer.event(
        "plan",
        classification=classify(files),
        files=[f.path for f in files],
        selection={k: v or "run" for k, v in selection.items()},
        no_tools=no_tools,
    )

    file_contents = await fetch_file_contents(gh, pr, files)

    async def correctness_task() -> AgentRun:
        return await _batched(
            lambda batch: correctness.run(
                pr, batch, file_contents, executor,
                client=client, tracer=tracer, use_tools=not no_tools,
            ),
            reviewable,
            lambda fd: len(correctness.build_context(pr, [fd], file_contents)),
        )

    async def security_task() -> AgentRun:
        return await security.run(
            pr, files, executor, client=client, tracer=tracer, use_tools=not no_tools
        )

    async def style_task() -> AgentRun:
        return await _batched(
            lambda batch: style.run(pr, batch, file_contents, client=client, tracer=tracer),
            reviewable,
            lambda fd: len(style.build_context(pr, [fd], file_contents)),
        )

    async def coverage_task() -> AgentRun:
        try:
            tree = await gh.get_tree(pr.owner, pr.repo, pr.head_sha)
            test_paths = [p for p in tree if looks_like_test(p)][:500]
        except httpx.HTTPError:
            test_paths = []
        test_contents = {p: c for p, c in file_contents.items() if looks_like_test(p)}
        return await test_coverage.run(
            pr, files, test_paths, test_contents, client=client, tracer=tracer
        )

    tasks = {
        "correctness": correctness_task,
        "security": security_task,
        "style": style_task,
        "test_coverage": coverage_task,
    }
    running = [name for name in AGENT_ORDER if selection[name] is None]
    results = dict(zip(
        running,
        await asyncio.gather(*(guarded(name, tasks[name]) for name in running)),
    ))

    out = []
    for name in AGENT_ORDER:
        if selection[name] is not None:
            out.append(AgentRun(
                agent=name, status="skipped", findings=[], tool_calls=0, tokens_in=0,
                tokens_out=0, cost_usd=0.0, duration_s=0.0,
                skip_or_error_reason=selection[name],
            ))
        else:
            out.append(results[name])
    return out

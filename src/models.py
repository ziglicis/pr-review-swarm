"""Core data model (SPEC.md §2.2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    agent: str  # "security", "correctness", ...
    file: str  # path within repo
    line_range: tuple | None
    severity: str  # "critical" | "major" | "minor" | "nit"
    title: str  # one-line summary
    detail: str  # explanation + suggested direction
    confidence: float  # 0.0-1.0, agent's self-assessed confidence


@dataclass
class AgentRun:
    agent: str
    status: str  # "ok" | "timeout" | "error" | "skipped"
    findings: list[Finding]
    tool_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_s: float
    skip_or_error_reason: str | None = None


@dataclass
class ReviewStats:
    total_tokens: int
    total_cost_usd: float
    duration_s: float


@dataclass
class Review:
    pr_url: str
    findings: list[Finding]  # post-synthesis, deduped & ranked
    summary: str
    agent_runs: list[AgentRun] = field(default_factory=list)
    stats: ReviewStats | None = None

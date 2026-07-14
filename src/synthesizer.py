"""Synthesizer: merge, dedupe, rank, and draft the final review.

LLM-driven merge (duplicates across reviewers need judgment); deterministic
sorting/filtering in Python. If synthesis fails, the raw agent findings are
returned unmerged — synthesis never sinks a review.
"""

from __future__ import annotations

import os
import time

from anthropic import AsyncAnthropic

from src.agents.base import (
    DEFAULT_MODEL,
    PRICE_IN_PER_MTOK,
    PRICE_OUT_PER_MTOK,
    SEVERITIES,
)
from src.github_client import PRData
from src.models import AgentRun, Finding, Review, ReviewStats

CONFIDENCE_THRESHOLD = 0.4  # default for now, future calibration analysis to revisit this
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

SYSTEM = """\
You are the review coordinator for a team of specialized code reviewers
(correctness, security, style, test coverage). You are given their raw findings
for one pull request, each with a stable id.

Produce the final review:
1. MERGE findings from different reviewers that point at the same underlying issue
   (cite all source ids). Keep genuinely distinct issues separate.
2. Do not drop any distinct issue, and do not invent new ones.
3. For a merged finding: severity = the highest among sources; confidence = your
   judgment given the sources; title/detail = one clear consolidated write-up.
4. If reviewers genuinely conflict (one says fine, one says broken), keep one
   finding, set disagreement=true, and summarize both positions in the detail.
5. Write a 2-3 sentence overall summary of the change and review outcome.
"""

SUBMIT_REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the final synthesized review. Call exactly once.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-3 sentence overall assessment"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ids of the raw findings merged into this one",
                        },
                        "file": {"type": "string"},
                        "line_start": {"type": "integer"},
                        "line_end": {"type": "integer"},
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "confidence": {"type": "number"},
                        "disagreement": {"type": "boolean"},
                    },
                    "required": [
                        "source_ids", "file", "line_start", "line_end",
                        "severity", "title", "detail", "confidence", "disagreement",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "findings"],
        "additionalProperties": False,
    },
}


def _serialize(findings: list[Finding]) -> str:
    parts = []
    for i, f in enumerate(findings):
        lo, hi = f.line_range
        parts.append(
            f"[f{i}] agent={f.agent} file={f.file} lines={lo}-{hi} "
            f"severity={f.severity} confidence={f.confidence}\n"
            f"title: {f.title}\ndetail: {f.detail}"
        )
    return "\n\n".join(parts)


def _validate(raw: list[dict], n_sources: int, valid_files: set[str]) -> list[str]:
    errors = []
    seen_ids: set[str] = set()
    for i, f in enumerate(raw):
        if not f["source_ids"]:
            errors.append(f"findings[{i}].source_ids is empty")
        for sid in f["source_ids"]:
            if not (sid.startswith("f") and sid[1:].isdigit() and int(sid[1:]) < n_sources):
                errors.append(f"findings[{i}] references unknown source id {sid!r}")
            elif sid in seen_ids:
                errors.append(f"source id {sid!r} is used by more than one merged finding")
            seen_ids.add(sid)
        if f["severity"] not in SEVERITIES:
            errors.append(f"findings[{i}].severity {f['severity']!r} invalid")
        if not 0.0 <= f["confidence"] <= 1.0:
            errors.append(f"findings[{i}].confidence {f['confidence']} outside 0.0-1.0")
        if f["file"] not in valid_files:
            errors.append(f"findings[{i}].file {f['file']!r} not changed in this PR")
    return errors


def _to_findings(raw: list[dict], source: list[Finding]) -> list[Finding]:
    out = []
    for f in raw:
        agents = sorted({source[int(sid[1:])].agent for sid in f["source_ids"]})
        detail = f["detail"]
        if f["disagreement"]:
            detail = f"**Reviewer disagreement.** {detail}"
        out.append(Finding(
            agent=", ".join(agents),
            file=f["file"],
            line_range=(f["line_start"], f["line_end"]),
            severity=f["severity"],
            title=f["title"],
            detail=detail,
            confidence=f["confidence"],
        ))
    return out


def _sort(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_RANK[f.severity], -f.confidence))


def _build_review(
    pr: PRData, findings: list[Finding], summary: str, runs: list[AgentRun]
) -> Review:
    total_in = sum(r.tokens_in for r in runs)
    total_out = sum(r.tokens_out for r in runs)
    agent_wall = max((r.duration_s for r in runs if r.agent != "synthesizer"), default=0.0)
    synth_s = sum(r.duration_s for r in runs if r.agent == "synthesizer")
    return Review(
        pr_url=f"https://github.com/{pr.owner}/{pr.repo}/pull/{pr.number}",
        findings=findings,
        summary=summary,
        agent_runs=runs,
        stats=ReviewStats(
            total_tokens=total_in + total_out,
            total_cost_usd=sum(r.cost_usd for r in runs),
            duration_s=agent_wall + synth_s,  # agents parallel, synthesis after
        ),
    )


async def synthesize(
    pr: PRData,
    runs: list[AgentRun],
    valid_files: set[str],
    client: AsyncAnthropic | None = None,
) -> Review:
    source = [f for r in runs if r.status == "ok" for f in r.findings]
    if not source:
        if any(r.status == "ok" for r in runs):
            summary = "No issues found by any reviewer."
        else:
            summary = "No review produced: every agent was skipped or failed."
        return _build_review(pr, [], summary, runs)

    client = client or AsyncAnthropic()
    start = time.monotonic()
    messages: list[dict] = [{
        "role": "user",
        "content": f"# PR: {pr.title}\n\n## Raw findings\n\n{_serialize(source)}",
    }]
    tokens_in = tokens_out = 0
    reason: str | None = None
    result: dict | None = None

    try:
        for attempt in range(2):  # initial call + one corrective retry
            resp = await client.messages.create(
                model=os.environ.get("PR_REVIEW_MODEL", DEFAULT_MODEL),
                max_tokens=8000,
                system=SYSTEM,
                tools=[SUBMIT_REVIEW_TOOL],
                tool_choice={"type": "tool", "name": "submit_review"},
                messages=messages,
            )
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            tool_use = next(b for b in resp.content if b.type == "tool_use")
            errors = _validate(tool_use.input["findings"], len(source), valid_files)
            if not errors:
                result = tool_use.input
                break
            reason = "; ".join(errors)
            messages += [
                {"role": "assistant", "content": resp.content},
                {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": (
                            f"Validation failed: {reason}. "
                            "Call submit_review again with corrections."
                        ),
                        "is_error": True,
                    }],
                },
            ]
    except Exception as e:  # synthesis failure never sinks the review
        reason = f"{type(e).__name__}: {e}"

    synth_run = AgentRun(
        agent="synthesizer",
        status="ok" if result is not None else "error",
        findings=[],
        tool_calls=0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=(tokens_in * PRICE_IN_PER_MTOK + tokens_out * PRICE_OUT_PER_MTOK) / 1_000_000,
        duration_s=time.monotonic() - start,
        skip_or_error_reason=None if result is not None else reason,
    )

    if result is None:  # fall back to the raw, unmerged findings
        return _build_review(
            pr,
            _sort(source),
            f"(Synthesis unavailable: {reason} — showing raw agent findings.)",
            [*runs, synth_run],
        )

    merged = _sort(_to_findings(result["findings"], source))
    kept = [f for f in merged if f.confidence >= CONFIDENCE_THRESHOLD]
    summary = result["summary"]
    if len(kept) < len(merged):
        summary += (
            f" ({len(merged) - len(kept)} finding(s) below the "
            f"{CONFIDENCE_THRESHOLD} confidence threshold hidden.)"
        )
    synth_run.findings = kept
    return _build_review(pr, kept, summary, [*runs, synth_run])

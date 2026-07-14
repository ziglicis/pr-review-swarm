"""Single-pass agent runner: prompt -> forced report_findings call -> validated Findings.

The tool loop for Correctness/Security lands in Phase 2; validation and accounting
here are built so the loop slots in without rewriting them.
"""

from __future__ import annotations

import os
import time

from anthropic import AsyncAnthropic

from src.models import AgentRun, Finding

DEFAULT_MODEL = "claude-sonnet-4-6"
# TODO: prices hardcoded for claude-sonnet-4-6 ($/MTok); revisit if MODEL changes
PRICE_IN_PER_MTOK = 3.00
PRICE_OUT_PER_MTOK = 15.00

SEVERITIES = ("critical", "major", "minor", "nit")

REPORT_FINDINGS_TOOL = {
    "name": "report_findings",
    "description": (
        "Report your complete list of code review findings. Call exactly once. "
        "Use an empty findings list if you found no issues."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Repo-relative file path"},
                        "line_start": {"type": "integer", "description": "First affected line"},
                        "line_end": {"type": "integer", "description": "Last affected line"},
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "title": {"type": "string", "description": "One-line summary"},
                        "detail": {
                            "type": "string",
                            "description": "Explanation and suggested direction",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Self-assessed confidence, 0.0-1.0",
                        },
                    },
                    "required": [
                        "file", "line_start", "line_end",
                        "severity", "title", "detail", "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    },
}


def validate_findings(raw: list[dict], valid_files: set[str]) -> list[str]:
    """Semantic checks the JSON schema can't express. Returns error strings (empty = valid)."""
    errors = []
    for i, f in enumerate(raw):
        if f["severity"] not in SEVERITIES:
            errors.append(f"findings[{i}].severity {f['severity']!r} not in {SEVERITIES}")
        if not 0.0 <= f["confidence"] <= 1.0:
            errors.append(f"findings[{i}].confidence {f['confidence']} outside 0.0-1.0")
        if f["file"] not in valid_files:
            errors.append(f"findings[{i}].file {f['file']!r} is not a file changed in this PR")
        if f["line_start"] > f["line_end"] or f["line_start"] < 1:
            errors.append(f"findings[{i}] line range {f['line_start']}-{f['line_end']} invalid")
    return errors


async def run_single_pass(
    agent_name: str,
    system: str,
    user_content: str,
    valid_files: set[str],
    client: AsyncAnthropic | None = None,
) -> AgentRun:
    """One forced report_findings call, semantic validation, one corrective retry."""
    client = client or AsyncAnthropic()
    start = time.monotonic()
    messages: list[dict] = [{"role": "user", "content": user_content}]
    tokens_in = tokens_out = calls = 0
    findings: list[Finding] | None = None
    reason: str | None = None

    try:
        for _attempt in range(2):  # initial call + at most one corrective retry
            resp = await client.messages.create(
                model=os.environ.get("PR_REVIEW_MODEL", DEFAULT_MODEL),
                max_tokens=8000,
                system=system,
                tools=[REPORT_FINDINGS_TOOL],
                tool_choice={"type": "tool", "name": "report_findings"},
                messages=messages,
            )
            calls += 1
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            tool_use = next(b for b in resp.content if b.type == "tool_use")
            raw = tool_use.input["findings"]
            errors = validate_findings(raw, valid_files)
            if not errors:
                findings = [
                    Finding(
                        agent=agent_name,
                        file=f["file"],
                        line_range=(f["line_start"], f["line_end"]),
                        severity=f["severity"],
                        title=f["title"],
                        detail=f["detail"],
                        confidence=f["confidence"],
                    )
                    for f in raw
                ]
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
                            "Call report_findings again with corrected findings."
                        ),
                        "is_error": True,
                    }],
                },
            ]
    except Exception as e:  # API/network failure -> recorded, never raised
        reason = f"{type(e).__name__}: {e}"

    ok = findings is not None
    return AgentRun(
        agent=agent_name,
        status="ok" if ok else "error",
        findings=findings or [],
        tool_calls=calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=(tokens_in * PRICE_IN_PER_MTOK + tokens_out * PRICE_OUT_PER_MTOK) / 1_000_000,
        duration_s=time.monotonic() - start,
        skip_or_error_reason=None if ok else reason,
    )

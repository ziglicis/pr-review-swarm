"""Agent runner: bounded investigation loop -> forced report_findings -> validated Findings.

Single-pass agents (Style, Test Coverage) use run_single_pass, which is the same loop
with no investigation tools (report_findings is forced on the first call).
"""

from __future__ import annotations

import os
import time

from anthropic import AsyncAnthropic

from src.models import AgentRun, Finding
from src.tools import ToolExecutor
from src.trace import Tracer, clip

DEFAULT_MODEL = "claude-sonnet-4-6"
# TODO: prices hardcoded for claude-sonnet-4-6 ($/MTok); revisit if MODEL changes
PRICE_IN_PER_MTOK = 3.00
PRICE_OUT_PER_MTOK = 15.00

MAX_INVESTIGATION_CALLS = 3  # per-agent tool budget
TOKEN_CEILING_IN = 50_000  # hard stop on cumulative input tokens across the loop

SEVERITIES = ("critical", "major", "minor", "nit")

REPORT_FINDINGS_TOOL = {
    "name": "report_findings",
    "description": (
        "Report your complete list of code review findings. Call exactly once when your "
        "review is done. Use an empty findings list if you found no issues."
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


def _to_findings(agent_name: str, raw: list[dict]) -> list[Finding]:
    return [
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


async def run_with_tools(
    agent_name: str,
    system: str,
    user_content: str,
    valid_files: set[str],
    executor: ToolExecutor | None = None,
    investigation_tools: list[dict] | None = None,
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
) -> AgentRun:
    """Bounded observe→decide→act loop ending in a validated report_findings call.

    With no investigation tools this degenerates to a single forced call
    (plus at most one corrective retry on validation failure).
    """
    client = client or AsyncAnthropic()
    tracer = tracer or Tracer()  # throwaway when not tracing
    investigation_tools = investigation_tools or []
    tracer.event(
        "agent_start", agent=agent_name,
        tools=[t["name"] for t in investigation_tools],
        system=clip(system), context=clip(user_content),
    )
    tools = [*investigation_tools, REPORT_FINDINGS_TOOL]
    start = time.monotonic()
    messages: list[dict] = [{"role": "user", "content": user_content}]
    tokens_in = tokens_out = investigations = validation_retries = 0
    must_report = not investigation_tools
    findings: list[Finding] | None = None
    reason: str | None = None

    try:
        while True:
            force = (
                must_report
                or investigations >= MAX_INVESTIGATION_CALLS
                or tokens_in >= TOKEN_CEILING_IN
            )
            resp = await client.messages.create(
                model=os.environ.get("PR_REVIEW_MODEL", DEFAULT_MODEL),
                max_tokens=8000,
                system=system,
                tools=tools,
                tool_choice=(
                    {"type": "tool", "name": "report_findings"} if force else {"type": "auto"}
                ),
                messages=messages,
            )
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            tracer.event(
                "model_call", agent=agent_name, forced=force,
                tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens,
                blocks=tracer.blocks(resp.content),
            )
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            report = next((b for b in tool_uses if b.name == "report_findings"), None)

            if report is not None:
                raw = report.input["findings"]
                errors = validate_findings(raw, valid_files)
                if not errors:
                    findings = _to_findings(agent_name, raw)
                    break
                reason = "; ".join(errors)
                tracer.event("validation_failed", agent=agent_name, errors=errors)
                if validation_retries >= 1:
                    break  # -> status "error" with the validation reason
                validation_retries += 1
                must_report = True
                messages += [
                    {"role": "assistant", "content": resp.content},
                    {
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": report.id,
                            "content": (
                                f"Validation failed: {reason}. "
                                "Call report_findings again with corrected findings."
                            ),
                            "is_error": True,
                        }],
                    },
                ]
                continue

            if not tool_uses:  # text-only turn: insist on a report next round
                must_report = True
                messages += [
                    {"role": "assistant", "content": resp.content},
                    {
                        "role": "user",
                        "content": "Call report_findings now with your complete findings.",
                    },
                ]
                continue

            # investigation tool calls
            results = []
            for tu in tool_uses:
                content, is_error = await executor.execute(tu.name, tu.input)
                tracer.event(
                    "tool_call", agent=agent_name, tool=tu.name, input=tu.input,
                    is_error=is_error, result=clip(content, 5000),
                )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content,
                    "is_error": is_error,
                })
            investigations += len(tool_uses)
            messages += [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": results},
            ]
    except Exception as e:  # API/network failure -> recorded, never raised
        reason = f"{type(e).__name__}: {e}"

    ok = findings is not None
    tracer.event(
        "agent_end", agent=agent_name, status="ok" if ok else "error",
        findings=len(findings or []), tool_calls=investigations,
        tokens_in=tokens_in, tokens_out=tokens_out, reason=reason if not ok else None,
    )
    return AgentRun(
        agent=agent_name,
        status="ok" if ok else "error",
        findings=findings or [],
        tool_calls=investigations,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=(tokens_in * PRICE_IN_PER_MTOK + tokens_out * PRICE_OUT_PER_MTOK) / 1_000_000,
        duration_s=time.monotonic() - start,
        skip_or_error_reason=None if ok else reason,
    )


async def run_single_pass(
    agent_name: str,
    system: str,
    user_content: str,
    valid_files: set[str],
    client: AsyncAnthropic | None = None,
    tracer: Tracer | None = None,
) -> AgentRun:
    """One forced report_findings call, semantic validation, one corrective retry."""
    return await run_with_tools(
        agent_name, system, user_content, valid_files, client=client, tracer=tracer
    )

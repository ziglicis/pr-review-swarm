from src.cli import format_review
from src.github_client import PRData
from src.models import AgentRun, Finding


def make_pr():
    return PRData(
        owner="o", repo="r", number=7, title="Fix widget",
        body=None, head_sha="abc", changed_lines=10, diff="diff --git\n",
    )


def make_run(**over):
    base = dict(
        agent="correctness", status="ok", findings=[], tool_calls=1,
        tokens_in=1000, tokens_out=200, cost_usd=0.006, duration_s=3.2,
        skip_or_error_reason=None,
    )
    return AgentRun(**{**base, **over})


def finding(severity, agent="correctness", confidence=0.7, line_start=5, line_end=5):
    return Finding(
        agent=agent, file="a.py", line_range=(line_start, line_end),
        severity=severity, title=f"{severity} issue", detail="Because.",
        confidence=confidence,
    )


def test_groups_by_severity_across_agents():
    runs = [
        make_run(findings=[finding("nit"), finding("critical")]),
        make_run(agent="security", findings=[finding("major", agent="security")]),
    ]
    md = format_review(make_pr(), runs)
    assert md.index("## Critical") < md.index("## Major") < md.index("## Nit")
    assert "_(security, confidence 0.7)_" in md


def test_sorted_by_confidence_within_severity():
    runs = [make_run(findings=[
        finding("major", confidence=0.5), finding("major", confidence=0.9),
    ])]
    md = format_review(make_pr(), runs)
    assert md.index("confidence 0.9") < md.index("confidence 0.5")


def test_line_range_rendering():
    md = format_review(make_pr(), [make_run(findings=[finding("major", line_end=9)])])
    assert "a.py:5-9" in md


def test_no_findings():
    md = format_review(make_pr(), [make_run()])
    assert "No issues found." in md


def test_agent_lines_for_all_statuses():
    runs = [
        make_run(),
        make_run(agent="security", status="timeout", skip_or_error_reason="exceeded 120s"),
        make_run(agent="style", status="skipped", skip_or_error_reason="no code files changed"),
    ]
    md = format_review(make_pr(), runs)
    assert "- correctness: 0 finding(s)" in md
    assert "- security: timeout — exceeded 120s" in md
    assert "- style: skipped — no code files changed" in md


def test_totals_line():
    runs = [make_run(), make_run(agent="style", tokens_in=500, duration_s=9.9)]
    md = format_review(make_pr(), runs)
    assert "_total: 1500 in / 400 out tokens" in md
    assert "9.9s wall" in md  # parallel: wall time is the max, not the sum

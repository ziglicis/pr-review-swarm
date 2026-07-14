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


def finding(severity, line_start=5, line_end=5):
    return Finding(
        agent="correctness", file="a.py", line_range=(line_start, line_end),
        severity=severity, title=f"{severity} issue", detail="Because.", confidence=0.7,
    )


def test_groups_by_severity_in_order():
    run = make_run(findings=[finding("nit"), finding("critical"), finding("major")])
    md = format_review(make_pr(), run)
    assert md.index("## Critical") < md.index("## Major") < md.index("## Nit")
    assert "- **a.py:5** — critical issue" in md
    assert "$0.0060" in md and "3 finding(s)" in md


def test_line_range_rendering():
    md = format_review(make_pr(), make_run(findings=[finding("major", 5, 9)]))
    assert "a.py:5-9" in md


def test_no_findings():
    md = format_review(make_pr(), make_run())
    assert "No correctness issues found." in md


def test_error_run_surfaces_reason():
    run = make_run(status="error", skip_or_error_reason="RuntimeError: boom")
    md = format_review(make_pr(), run)
    assert "unavailable" in md and "boom" in md

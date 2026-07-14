from src.cli import format_review
from src.github_client import PRData
from src.models import AgentRun, Finding, Review, ReviewStats


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


def make_review(findings=(), summary="Looks fine.", runs=None):
    return Review(
        pr_url="https://github.com/o/r/pull/7",
        findings=list(findings),
        summary=summary,
        agent_runs=runs or [make_run()],
        stats=ReviewStats(total_tokens=1200, total_cost_usd=0.006, duration_s=3.2),
    )


def test_summary_shown_up_top():
    md = format_review(make_pr(), make_review(summary="Two reviewers flagged X."))
    assert md.index("Two reviewers flagged X.") < md.index("## Agent runs")


def test_groups_by_severity():
    review = make_review(findings=[
        finding("critical"), finding("major", agent="correctness, security"), finding("nit"),
    ])
    md = format_review(make_pr(), review)
    assert md.index("## Critical") < md.index("## Major") < md.index("## Nit")
    assert "_(correctness, security, confidence 0.7)_" in md


def test_line_range_rendering():
    md = format_review(make_pr(), make_review(findings=[finding("major", line_end=9)]))
    assert "a.py:5-9" in md


def test_agent_lines_for_all_statuses():
    runs = [
        make_run(),
        make_run(agent="security", status="timeout", skip_or_error_reason="exceeded 120s"),
        make_run(agent="style", status="skipped", skip_or_error_reason="no code files changed"),
        make_run(agent="synthesizer"),
    ]
    md = format_review(make_pr(), make_review(runs=runs))
    assert "- correctness: 0 finding(s)" in md
    assert "- security: timeout — exceeded 120s" in md
    assert "- style: skipped — no code files changed" in md
    assert "- synthesizer: 0 finding(s)" in md


def test_totals_from_stats():
    md = format_review(make_pr(), make_review())
    assert "_total: 1200 tokens · $0.0060 · 3.2s_" in md

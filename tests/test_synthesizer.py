from types import SimpleNamespace

from src.github_client import PRData
from src.models import AgentRun, Finding
from src.synthesizer import CONFIDENCE_THRESHOLD, synthesize

PR = PRData(
    owner="o", repo="r", number=1, title="T", body=None,
    head_sha="s", changed_lines=5, diff="",
)
VALID_FILES = {"a.py"}


def mkfinding(agent, severity="major", confidence=0.8, title=None):
    return Finding(
        agent=agent, file="a.py", line_range=(5, 6), severity=severity,
        title=title or f"{agent} issue", detail="Because.", confidence=confidence,
    )


def mkrun(agent, findings=(), status="ok"):
    return AgentRun(
        agent=agent, status=status, findings=list(findings), tool_calls=0,
        tokens_in=100, tokens_out=10, cost_usd=0.001, duration_s=2.0,
        skip_or_error_reason=None if status == "ok" else "why",
    )


def merged(source_ids, severity="major", confidence=0.9, disagreement=False):
    return {
        "source_ids": source_ids, "file": "a.py", "line_start": 5, "line_end": 6,
        "severity": severity, "title": "Merged issue", "detail": "Combined.",
        "confidence": confidence, "disagreement": disagreement,
    }


class StubClient:
    def __init__(self, payloads):
        self.calls = []
        self._payloads = list(payloads)
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name="submit_review", id=f"t{len(self.calls)}",
            input=self._payloads.pop(0),
        )
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=500, output_tokens=50)
        )


class BoomClient:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        raise RuntimeError("api down")


RUNS = [
    mkrun("correctness", [mkfinding("correctness")]),
    mkrun("security", [mkfinding("security"), mkfinding("security", title="unique sec")]),
]


async def test_merge_and_labels():
    payload = {"summary": "Solid change.", "findings": [
        merged(["f0", "f1"]), merged(["f2"], severity="minor", confidence=0.6),
    ]}
    review = await synthesize(PR, RUNS, VALID_FILES, client=StubClient([payload]))
    assert review.summary.startswith("Solid change.")
    assert len(review.findings) == 2
    assert review.findings[0].agent == "correctness, security"  # merged across agents
    assert review.findings[0].severity == "major"  # sorted: major before minor
    assert review.agent_runs[-1].agent == "synthesizer"
    assert review.agent_runs[-1].status == "ok"


async def test_confidence_filter_and_summary_note():
    payload = {"summary": "OK.", "findings": [
        merged(["f0"]), merged(["f1"], confidence=CONFIDENCE_THRESHOLD - 0.1),
        merged(["f2"], confidence=0.5),
    ]}
    review = await synthesize(PR, RUNS, VALID_FILES, client=StubClient([payload]))
    assert len(review.findings) == 2
    assert "1 finding(s) below" in review.summary


async def test_disagreement_marked():
    payload = {"summary": "OK.", "findings": [
        merged(["f0", "f1", "f2"], disagreement=True),
    ]}
    review = await synthesize(PR, RUNS, VALID_FILES, client=StubClient([payload]))
    assert review.findings[0].detail.startswith("**Reviewer disagreement.**")


async def test_validation_retry():
    bad = {"summary": "x", "findings": [merged(["f9"])]}  # unknown source id
    good = {"summary": "Fixed.", "findings": [merged(["f0", "f1", "f2"])]}
    client = StubClient([bad, good])
    review = await synthesize(PR, RUNS, VALID_FILES, client=client)
    assert len(client.calls) == 2
    assert review.agent_runs[-1].status == "ok"
    assert review.summary.startswith("Fixed.")


async def test_fallback_on_api_failure():
    review = await synthesize(PR, RUNS, VALID_FILES, client=BoomClient())
    assert "Synthesis unavailable" in review.summary
    assert len(review.findings) == 3  # raw findings, unmerged
    assert review.agent_runs[-1].agent == "synthesizer"
    assert review.agent_runs[-1].status == "error"


async def test_no_findings_skips_llm():
    runs = [mkrun("correctness"), mkrun("security", status="skipped")]
    review = await synthesize(PR, runs, VALID_FILES, client=BoomClient())  # never called
    assert review.summary == "No issues found by any reviewer."
    assert review.findings == []
    assert all(r.agent != "synthesizer" for r in review.agent_runs)


async def test_all_failed():
    runs = [mkrun("correctness", status="error"), mkrun("security", status="skipped")]
    review = await synthesize(PR, runs, VALID_FILES, client=BoomClient())
    assert "skipped or failed" in review.summary


async def test_stats_aggregate():
    payload = {"summary": "OK.", "findings": [merged(["f0", "f1", "f2"])]}
    review = await synthesize(PR, RUNS, VALID_FILES, client=StubClient([payload]))
    # 2 agent runs (100+10 each) + synthesizer (500+50)
    assert review.stats.total_tokens == 220 + 550
    assert review.stats.duration_s >= 2.0  # agent wall (max) + synth time

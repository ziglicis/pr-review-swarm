from types import SimpleNamespace

from eval import baseline_agent
from eval.run_eval import build_worksheet, load_dataset, result_to_dict
from src.diff_parser import parse_diff
from src.github_client import PRData
from src.models import AgentRun, Finding

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 a
+b
 c
"""


def make_pr():
    return PRData(
        owner="o", repo="r", number=1, title="T", body=None,
        head_sha="s" * 40, changed_lines=3, diff=DIFF,
    )


def make_finding(confidence=0.8, severity="major"):
    return Finding(
        agent="baseline", file="src/app.py", line_range=(2, 2), severity=severity,
        title="Issue", detail="Because.", confidence=confidence,
    )


def make_run(findings):
    return AgentRun(
        agent="baseline", status="ok", findings=findings, tool_calls=1,
        tokens_in=100, tokens_out=10, cost_usd=0.01, duration_s=1.0,
        skip_or_error_reason=None,
    )


def test_load_dataset_all_cases():
    cases = load_dataset()
    assert len(cases) == 15
    assert cases[0]["case"] < cases[-1]["case"]  # sorted
    assert all("ground_truth" in c for c in cases)


def test_load_dataset_filter():
    cases = load_dataset("httpx")
    assert len(cases) == 5
    assert all(c["repo"] == "encode/httpx" for c in cases)


def canned_case():
    return {
        "case": "requests_9999",
        "url": "https://github.com/psf/requests/pull/9999",
        "pre_merge_sha": "s" * 40,
        "ground_truth": [
            {"id": "g1", "issue": "bad thing", "evidence": "https://x", "category": "correctness"},
            {"id": "g2", "issue": "other thing", "evidence": "https://x", "category": "test"},
        ],
    }


def test_result_to_dict_and_worksheet():
    case = canned_case()
    run = make_run([make_finding()])
    result = result_to_dict(case, "full", run.findings, [run], "Summary.", "s" * 40)
    assert result["sha_matches_dataset"] is True
    assert result["findings"][0]["line_start"] == 2
    assert result["totals"]["cost_usd"] == 0.01

    ws = build_worksheet(case, result)
    assert [m["ground_truth"] for m in ws["ground_truth_matches"]] == ["g1", "g2"]
    assert all(m["matched_finding"] is None for m in ws["ground_truth_matches"])
    assert ws["finding_validity"][0]["finding"] == 0
    assert ws["finding_validity"][0]["valid"] is None


def test_sha_mismatch_flagged():
    result = result_to_dict(canned_case(), "full", [], [], "", "different-sha")
    assert result["sha_matches_dataset"] is False


class ReportingClient:
    def __init__(self, findings_payload):
        self.calls = []
        self._payload = findings_payload
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name="report_findings", id="t1",
            input={"findings": self._payload},
        )
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=10)
        )


async def test_baseline_filters_low_confidence_and_sorts():
    payload = [
        {"file": "src/app.py", "line_start": 2, "line_end": 2, "severity": "minor",
         "title": "keep-minor", "detail": "d", "confidence": 0.9},
        {"file": "src/app.py", "line_start": 2, "line_end": 2, "severity": "critical",
         "title": "keep-critical", "detail": "d", "confidence": 0.6},
        {"file": "src/app.py", "line_start": 2, "line_end": 2, "severity": "major",
         "title": "drop-me", "detail": "d", "confidence": 0.2},
    ]
    client = ReportingClient(payload)
    run = await baseline_agent.run(
        make_pr(), parse_diff(DIFF), {}, [], executor=None, client=client
    )
    assert [f.title for f in run.findings] == ["keep-critical", "keep-minor"]
    # generalist gets all three investigation tools + report_findings
    tools = [t["name"] for t in client.calls[0]["tools"]]
    assert tools == ["expand_context", "read_file", "list_files", "report_findings"]


def test_baseline_context_has_union_sections():
    ctx = baseline_agent.build_context(
        make_pr(), parse_diff(DIFF), {}, ["tests/test_app.py"]
    )
    assert "## All files changed in this PR" in ctx
    assert "- tests/test_app.py" in ctx
    assert "+b" in ctx  # hunks present


async def test_no_tools_flag_removes_investigation_tools():
    from src.agents import correctness

    client = ReportingClient([])
    await correctness.run(
        make_pr(), parse_diff(DIFF), {}, executor=None, client=client, use_tools=False
    )
    tools = [t["name"] for t in client.calls[0]["tools"]]
    assert tools == ["report_findings"]
    # with no investigation tools the report is forced immediately
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "report_findings"}

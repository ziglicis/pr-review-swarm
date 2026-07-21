from types import SimpleNamespace

from src.agents.base import validate_findings, run_single_pass, run_with_tools
from src.agents.correctness import build_context
from src.diff_parser import parse_diff
from src.github_client import PRData

GOOD = {
    "file": "src/app.py",
    "line_start": 10,
    "line_end": 12,
    "severity": "major",
    "title": "Off-by-one in loop bound",
    "detail": "range(n) should be range(n + 1).",
    "confidence": 0.8,
}
VALID_FILES = {"src/app.py"}


def test_validate_ok():
    assert validate_findings([GOOD], VALID_FILES) == []
    assert validate_findings([], VALID_FILES) == []


def test_validate_catches_errors():
    bad = [
        {**GOOD, "severity": "blocker"},
        {**GOOD, "confidence": 1.5},
        {**GOOD, "file": "not/in/diff.py"},
        {**GOOD, "line_start": 12, "line_end": 10},
    ]
    errors = validate_findings(bad, VALID_FILES)
    assert len(errors) == 4


class StubClient:
    """Mimics AsyncAnthropic just enough: returns queued findings payloads in order."""

    def __init__(self, payloads):
        self.calls = []
        self._payloads = list(payloads)
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name="report_findings", id=f"toolu_{len(self.calls)}",
            input={"findings": self._payloads.pop(0)},
        )
        return SimpleNamespace(
            content=[block],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


async def test_single_pass_ok():
    client = StubClient([[GOOD]])
    run = await run_single_pass("correctness", "sys", "ctx", VALID_FILES, client=client)
    assert run.status == "ok"
    assert run.tool_calls == 0  # no investigation tools in single-pass mode
    assert len(client.calls) == 1
    assert run.findings[0].line_range == (10, 12)
    assert run.findings[0].agent == "correctness"
    assert run.tokens_in == 1000 and run.tokens_out == 100
    assert abs(run.cost_usd - (1000 * 3 + 100 * 15) / 1e6) < 1e-9
    # forced tool choice on the request
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "report_findings"}


async def test_corrective_retry_then_ok():
    client = StubClient([[{**GOOD, "severity": "blocker"}], [GOOD]])
    run = await run_single_pass("correctness", "sys", "ctx", VALID_FILES, client=client)
    assert run.status == "ok"
    assert len(client.calls) == 2
    # retry message carries the validation error back
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-1]["content"][0]["is_error"] is True
    assert "blocker" in retry_messages[-1]["content"][0]["content"]


async def test_retry_exhausted_is_error_not_raise():
    bad = [{**GOOD, "confidence": 2.0}]
    client = StubClient([bad, bad])
    run = await run_single_pass("correctness", "sys", "ctx", VALID_FILES, client=client)
    assert run.status == "error"
    assert run.findings == []
    assert "confidence" in run.skip_or_error_reason
    assert len(client.calls) == 2  # initial + one corrective retry, then gave up


async def test_truncated_tool_input_retries_not_crashes():
    # input missing 'findings' entirely (e.g. output cut off mid-call)
    client = StubClient([[GOOD]])
    client._payloads = []  # bypass payload queue; craft raw responses instead

    responses = [{}, {"findings": [GOOD]}]

    async def create(**kwargs):
        client.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name="report_findings",
            id=f"t{len(client.calls)}", input=responses.pop(0),
        )
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=10)
        )

    client.messages = SimpleNamespace(create=create)
    run = await run_single_pass("correctness", "sys", "ctx", VALID_FILES, client=client)
    assert run.status == "ok"
    assert len(client.calls) == 2
    assert "truncated" in client.calls[1]["messages"][-1]["content"][0]["content"]


async def test_report_alongside_investigation_call_answers_all_tool_uses():
    # Auto-mode turn returns an investigation call AND report_findings together,
    # and the report fails validation. The retry must emit a tool_result for BOTH
    # tool_use ids, or the next request 400s (the flask_2570 baseline crash).
    bad = {**GOOD, "severity": "blocker"}
    turns = [
        [  # co-occurring investigation + invalid report
            SimpleNamespace(type="tool_use", name="read_file", id="inv_1",
                            input={"path": "src/app.py"}),
            SimpleNamespace(type="tool_use", name="report_findings", id="rep_1",
                            input={"findings": [bad]}),
        ],
        [SimpleNamespace(type="tool_use", name="report_findings", id="rep_2",
                         input={"findings": [GOOD]})],
    ]
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=turns.pop(0),
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10))

    client = SimpleNamespace(messages=SimpleNamespace(create=create))

    class FakeExecutor:
        async def execute(self, name, inp):
            return ("file contents", False)

    run = await run_with_tools(
        "correctness", "sys", "ctx", VALID_FILES,
        executor=FakeExecutor(),
        investigation_tools=[{"name": "read_file"}],
        client=client,
    )
    assert run.status == "ok"
    assert len(calls) == 2
    # every tool_use id from the first turn is answered in the retry user message
    answered = {tr["tool_use_id"] for tr in calls[1]["messages"][-1]["content"]}
    assert answered == {"inv_1", "rep_1"}


async def test_api_exception_recorded_not_raised():
    class Boom:
        def __init__(self):
            self.messages = SimpleNamespace(create=self._create)

        async def _create(self, **kwargs):
            raise RuntimeError("connection reset")

    run = await run_single_pass("correctness", "sys", "ctx", VALID_FILES, client=Boom())
    assert run.status == "error"
    assert "connection reset" in run.skip_or_error_reason


DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -50,3 +50,4 @@ def f():
 a
-b
+B
+C
 c
"""


def make_pr(**over):
    base = dict(
        owner="o", repo="r", number=1, title="T", body="desc",
        head_sha="s", changed_lines=4, diff=DIFF,
    )
    return PRData(**{**base, **over})


def test_build_context_expands_to_file_lines():
    files = parse_diff(DIFF)
    content = "\n".join(f"line{n}" for n in range(1, 101))
    ctx = build_context(make_pr(), files, {"src/app.py": content})
    # ±30 window around hunk at new lines 50-53: before starts at 20, after ends at 83
    assert "   20  line20" in ctx
    assert "   83  line83" in ctx
    assert "   19  line19" not in ctx
    assert "   84  line84" not in ctx
    assert "+B" in ctx and "-b" in ctx  # the hunk itself, annotated


def test_build_context_without_file_content_still_has_hunks():
    files = parse_diff(DIFF)
    ctx = build_context(make_pr(), files, {})
    assert "+C" in ctx
    assert "## src/app.py (modified)" in ctx

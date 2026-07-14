from types import SimpleNamespace

import httpx

from src.agents.base import MAX_INVESTIGATION_CALLS, run_with_tools
from src.github_client import PRData
from src.tools import EXPAND_CONTEXT_TOOL, READ_FILE_TOOL, MAX_FILE_LINES, ToolExecutor

GOOD = {
    "file": "src/app.py",
    "line_start": 10,
    "line_end": 12,
    "severity": "major",
    "title": "Bug",
    "detail": "Because.",
    "confidence": 0.8,
}
VALID_FILES = {"src/app.py"}
TOOLS = [EXPAND_CONTEXT_TOOL, READ_FILE_TOOL]


def tool_block(name, input, i):
    return SimpleNamespace(type="tool_use", name=name, id=f"toolu_{i}", input=input)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def investigate(i):
    return [tool_block("read_file", {"path": "src/app.py"}, i)]


def report(findings, i=99):
    return [tool_block("report_findings", {"findings": findings}, i)]


class ScriptedClient:
    """Returns scripted content-block lists in order; records every request."""

    def __init__(self, script, usage=None):
        self.calls = []
        self._script = list(script)
        self._usage = list(usage or [])
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        tokens = self._usage.pop(0) if self._usage else 1000
        return SimpleNamespace(
            content=self._script.pop(0),
            usage=SimpleNamespace(input_tokens=tokens, output_tokens=100),
        )


class FakeExecutor:
    def __init__(self, result=("file contents", False)):
        self.calls = []
        self._result = result

    async def execute(self, name, args):
        self.calls.append((name, args))
        return self._result


async def run(client, executor=None):
    return await run_with_tools(
        "correctness", "sys", "ctx", VALID_FILES,
        executor=executor or FakeExecutor(), investigation_tools=TOOLS, client=client,
    )


async def test_budget_forces_report_after_max_calls():
    client = ScriptedClient([investigate(1), investigate(2), investigate(3), report([GOOD])])
    executor = FakeExecutor()
    result = await run(client, executor)
    assert result.status == "ok"
    assert result.tool_calls == MAX_INVESTIGATION_CALLS
    assert len(executor.calls) == 3
    # first request offers a choice; after the budget is spent the report is forced
    assert client.calls[0]["tool_choice"] == {"type": "auto"}
    assert client.calls[3]["tool_choice"] == {"type": "tool", "name": "report_findings"}


async def test_token_ceiling_forces_report():
    client = ScriptedClient([investigate(1), report([GOOD])], usage=[60_000, 1000])
    result = await run(client)
    assert result.status == "ok"
    assert result.tool_calls == 1
    assert client.calls[1]["tool_choice"] == {"type": "tool", "name": "report_findings"}


async def test_tool_results_fed_back():
    client = ScriptedClient([investigate(1), report([])])
    executor = FakeExecutor(result=("GitHub returned 404", True))
    await run(client, executor)
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1"
    assert tool_result["is_error"] is True


async def test_text_only_turn_gets_nudged():
    client = ScriptedClient([[text_block("I think I'm done.")], report([GOOD])])
    result = await run(client)
    assert result.status == "ok"
    assert client.calls[1]["tool_choice"] == {"type": "tool", "name": "report_findings"}
    assert "report_findings" in client.calls[1]["messages"][-1]["content"]


async def test_invalid_report_retries_then_forced():
    client = ScriptedClient([report([{**GOOD, "severity": "blocker"}], 1), report([GOOD], 2)])
    result = await run(client)
    assert result.status == "ok"
    assert client.calls[1]["tool_choice"] == {"type": "tool", "name": "report_findings"}


# --- ToolExecutor ---------------------------------------------------------

PR = PRData(
    owner="o", repo="r", number=1, title="t", body=None,
    head_sha="sha", changed_lines=1, diff="",
)


def gh_stub(content="a\nb\nc", tree=None, raise_status=None):
    async def get_file(owner, repo, path, ref):
        if raise_status:
            req = httpx.Request("GET", "https://api.github.com/x")
            raise httpx.HTTPStatusError(
                "err", request=req, response=httpx.Response(raise_status, request=req)
            )
        return content

    async def get_tree(owner, repo, sha):
        return tree or ["a.py", "b.py"]

    return SimpleNamespace(get_file=get_file, get_tree=get_tree)


async def test_read_file_numbers_lines():
    out, is_error = await ToolExecutor(gh_stub(), PR).execute("read_file", {"path": "x.py"})
    assert not is_error
    assert out.splitlines()[0].strip() == "1  a"


async def test_read_file_truncates():
    big = "\n".join("x" for _ in range(MAX_FILE_LINES + 10))
    out, is_error = await ToolExecutor(gh_stub(big), PR).execute("read_file", {"path": "x.py"})
    assert not is_error
    assert "truncated" in out


async def test_expand_context_clamps_range():
    executor = ToolExecutor(gh_stub("a\nb\nc"), PR)
    out, is_error = await executor.execute(
        "expand_context", {"file": "x.py", "line_start": 2, "line_end": 99}
    )
    assert not is_error
    assert out.splitlines()[-1].strip() == "3  c"


async def test_expand_context_empty_range():
    executor = ToolExecutor(gh_stub("a"), PR)
    out, is_error = await executor.execute(
        "expand_context", {"file": "x.py", "line_start": 5, "line_end": 9}
    )
    assert not is_error
    assert "is empty" in out


async def test_list_files():
    out, is_error = await ToolExecutor(gh_stub(), PR).execute("list_files", {})
    assert not is_error
    assert out == "a.py\nb.py"


async def test_404_is_error_string_not_raise():
    executor = ToolExecutor(gh_stub(raise_status=404), PR)
    out, is_error = await executor.execute("read_file", {"path": "nope.py"})
    assert is_error
    assert "404" in out


async def test_unknown_tool():
    out, is_error = await ToolExecutor(gh_stub(), PR).execute("bogus", {})
    assert is_error

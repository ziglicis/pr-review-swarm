import asyncio
from types import SimpleNamespace

from src.diff_parser import FileDiff, parse_diff
from src.github_client import PRData
from src.models import AgentRun
from src.orchestrator import (
    AGENT_ORDER,
    classify,
    file_kind,
    guarded,
    merge_runs,
    run_review,
    security_relevant,
    select_agents,
    split_batches,
)


def fd(path, **over):
    return FileDiff(path=path, old_path=path, **over)


# --- classification -------------------------------------------------------

def test_file_kind():
    assert file_kind("README.md") == "doc"
    assert file_kind("docs/api.html") == "doc"
    assert file_kind(".github/workflows/ci.yml") == "config"
    assert file_kind(".gitignore") == "config"
    assert file_kind("pyproject.toml") == "config"
    assert file_kind("src/app.py") == "code"
    assert file_kind("Dockerfile") == "code"


def test_classify():
    assert classify([fd("README.md"), fd("docs/x.rst")]) == "docs-only"
    assert classify([fd("pyproject.toml"), fd("README.md")]) == "config-only"
    assert classify([fd("src/a.py")]) == "code"
    assert classify([fd("src/a.py"), fd("README.md")]) == "mixed"


def test_security_relevant():
    assert not security_relevant([fd("README.md")])
    assert security_relevant([fd("src/a.py")])
    assert security_relevant([fd("requirements.txt")])
    assert security_relevant([fd(".github/workflows/ci.yml")])
    assert security_relevant([fd("docker/Dockerfile.prod")])


# --- selection ------------------------------------------------------------

def test_docs_only_skips_everything():
    selection = select_agents([fd("README.md")])
    assert all(reason is not None for reason in selection.values())
    assert selection["correctness"] == "docs-only change"


def test_ci_config_runs_security_only():
    selection = select_agents([fd(".github/workflows/ci.yml")])
    assert selection["security"] is None
    assert selection["correctness"] == "config-only change"
    assert selection["style"] == "no code files changed"
    assert selection["test_coverage"] == "no non-test source files changed"


def test_test_only_pr_skips_coverage():
    selection = select_agents([fd("tests/test_app.py")])
    assert selection["correctness"] is None
    assert selection["style"] is None
    assert selection["test_coverage"] == "no non-test source files changed"


def test_code_pr_runs_all():
    selection = select_agents([fd("src/app.py")])
    assert all(reason is None for reason in selection.values())


# --- batching / merging ----------------------------------------------------

def test_split_batches_respects_budget():
    files = [fd("a"), fd("b"), fd("c")]
    sizes = {"a": 50, "b": 60, "c": 30}
    batches = split_batches(files, lambda f: sizes[f.path], budget=100)
    assert [[f.path for f in b] for b in batches] == [["a"], ["b", "c"]]


def test_split_batches_oversized_file_gets_own_batch():
    files = [fd("a"), fd("huge"), fd("b")]
    sizes = {"a": 10, "huge": 500, "b": 10}
    batches = split_batches(files, lambda f: sizes[f.path], budget=100)
    assert [[f.path for f in b] for b in batches] == [["a"], ["huge"], ["b"]]


def make_run(**over):
    base = dict(
        agent="correctness", status="ok", findings=[], tool_calls=1, tokens_in=100,
        tokens_out=10, cost_usd=0.01, duration_s=1.0, skip_or_error_reason=None,
    )
    return AgentRun(**{**base, **over})


def test_merge_runs_sums_accounting():
    merged = merge_runs([make_run(), make_run(tokens_in=200, cost_usd=0.02)])
    assert merged.status == "ok"
    assert merged.tokens_in == 300
    assert abs(merged.cost_usd - 0.03) < 1e-9


def test_merge_runs_partial_failure():
    merged = merge_runs([make_run(), make_run(status="error", skip_or_error_reason="boom")])
    assert merged.status == "error"
    assert "1/2 batches failed" in merged.skip_or_error_reason


# --- failure isolation ------------------------------------------------------

async def test_guarded_catches_exceptions():
    async def boom():
        raise RuntimeError("agent exploded")

    run = await guarded("security", boom)
    assert run.status == "error"
    assert "agent exploded" in run.skip_or_error_reason


async def test_guarded_times_out():
    async def slow():
        await asyncio.sleep(1)

    run = await guarded("security", slow, timeout=0.01)
    assert run.status == "timeout"
    assert "timeout" in run.skip_or_error_reason


# --- run_review end-to-end (stubbed) ----------------------------------------

CODE_DIFF = """\
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
        head_sha="s", changed_lines=3, diff=CODE_DIFF,
    )


class ReportingClient:
    """Every create() immediately reports zero findings."""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name="report_findings", id="t1", input={"findings": []}
        )
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=10)
        )


def gh_stub():
    async def get_file(owner, repo, path, ref):
        return "a\nb\nc"

    async def get_tree(owner, repo, sha):
        return ["src/app.py", "tests/test_app.py"]

    return SimpleNamespace(get_file=get_file, get_tree=get_tree)


async def test_run_review_all_agents_on_code_pr():
    client = ReportingClient()
    runs = await run_review(make_pr(), parse_diff(CODE_DIFF), gh_stub(), client=client)
    assert [r.agent for r in runs] == list(AGENT_ORDER)
    assert all(r.status == "ok" for r in runs)
    assert len(client.calls) == 4  # one forced/auto call per agent


async def test_run_review_docs_only_makes_no_api_calls():
    docs_diff = CODE_DIFF.replace("src/app.py", "README.md")
    client = ReportingClient()
    runs = await run_review(make_pr(), parse_diff(docs_diff), gh_stub(), client=client)
    assert all(r.status == "skipped" for r in runs)
    assert client.calls == []


async def test_run_review_one_agent_failing_leaves_others_ok(monkeypatch):
    from src.agents import security

    async def broken(*args, **kwargs):
        raise RuntimeError("security agent crashed")

    monkeypatch.setattr(security, "run", broken)
    client = ReportingClient()
    runs = await run_review(make_pr(), parse_diff(CODE_DIFF), gh_stub(), client=client)
    by_agent = {r.agent: r for r in runs}
    assert by_agent["security"].status == "error"
    assert "crashed" in by_agent["security"].skip_or_error_reason
    assert by_agent["correctness"].status == "ok"
    assert by_agent["style"].status == "ok"
    assert by_agent["test_coverage"].status == "ok"

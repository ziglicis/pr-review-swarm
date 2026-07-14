from types import SimpleNamespace

from src.agents import security, style, test_coverage
from src.diff_parser import parse_diff
from src.github_client import PRData

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,3 +10,4 @@ def f():
 a
-b
+B
+C
 c
diff --git a/docs/notes.md b/docs/notes.md
index 3333333..4444444 100644
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1,2 +1,2 @@
-old docs
+new docs
 more
"""


def make_pr():
    return PRData(
        owner="o", repo="r", number=1, title="T", body=None,
        head_sha="s", changed_lines=6, diff=DIFF,
    )


def test_security_context_lists_all_files_but_skips_doc_hunks():
    ctx = security.build_context(make_pr(), parse_diff(DIFF))
    assert "- src/app.py (modified)" in ctx
    assert "- docs/notes.md (modified)" in ctx  # listed, so odd additions are visible
    assert "+B" in ctx  # code hunk shown
    assert "+new docs" not in ctx  # docs hunk content omitted


def test_style_sample_excludes_hunk_lines_and_caps():
    (fd, _) = parse_diff(DIFF)
    lines = [f"line{n}" for n in range(1, 201)]
    sample = style.sample_untouched(fd, lines, limit=50)
    sampled_nos = [int(row.split()[0]) for row in sample.splitlines()]
    assert len(sampled_nos) == 50
    # hunk covers new lines 10-13
    assert not set(sampled_nos) & {10, 11, 12, 13}
    assert sampled_nos[0] == 1 and sampled_nos[9] == 14  # skips straight over the hunk


def test_style_context_includes_sample_section():
    files = parse_diff(DIFF)
    content = "\n".join(f"line{n}" for n in range(1, 30))
    ctx = style.build_context(make_pr(), files, {"src/app.py": content})
    assert "existing style sample" in ctx
    assert "+B" in ctx


async def test_style_severity_clamped():
    finding = {
        "file": "src/app.py", "line_start": 10, "line_end": 10, "severity": "critical",
        "title": "Bad name", "detail": "x", "confidence": 0.9,
    }
    block = SimpleNamespace(
        type="tool_use", name="report_findings", id="t1", input={"findings": [finding]}
    )
    resp = SimpleNamespace(
        content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=10)
    )

    async def create(**kwargs):
        return resp

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = await style.run(make_pr(), parse_diff(DIFF), {}, client=client)
    assert result.status == "ok"
    assert result.findings[0].severity == "minor"


def test_looks_like_test():
    assert test_coverage.looks_like_test("tests/test_app.py")
    assert test_coverage.looks_like_test("src/foo/__tests__/bar.test.js")
    assert test_coverage.looks_like_test("pkg/util_test.py")
    assert not test_coverage.looks_like_test("src/app.py")
    assert not test_coverage.looks_like_test("testimonials/quotes.py")


def test_coverage_context_sections():
    ctx = test_coverage.build_context(
        make_pr(),
        parse_diff(DIFF),
        repo_test_paths=["tests/test_app.py"],
        test_file_contents={"tests/test_app.py": "def test_f(): ..."},
    )
    assert "(modified, source file)" in ctx
    assert "- tests/test_app.py" in ctx
    assert "Full contents of changed test file: tests/test_app.py" in ctx
    assert "def test_f(): ..." in ctx


def test_coverage_context_no_tests_found():
    ctx = test_coverage.build_context(make_pr(), parse_diff(DIFF), [], {})
    assert "(none)" in ctx

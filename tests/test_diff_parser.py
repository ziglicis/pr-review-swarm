from pathlib import Path

from src.diff_parser import parse_diff

FIXTURE_DIFF = (Path(__file__).parent / "fixtures" / "pr.diff").read_text()

ADDED = """\
diff --git a/newfile.py b/newfile.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+print("hi")
+print("bye")
"""

DELETED = """\
diff --git a/gone.py b/gone.py
deleted file mode 100644
index e69de29..0000000
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-print("hi")
"""

RENAMED = """\
diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""

BINARY = """\
diff --git a/logo.png b/logo.png
index 1111111..2222222 100644
Binary files a/logo.png and b/logo.png differ
"""


def test_fixture_pr():
    files = parse_diff(FIXTURE_DIFF)
    assert len(files) == 1
    f = files[0]
    assert f.path == "src/requests/adapters.py"
    assert f.status == "modified"
    assert not f.is_binary
    assert len(f.hunks) == 5
    # every hunk's captured line count matches its @@ header
    for h in f.hunks:
        new_side = [ln for ln in h.lines if not ln.startswith(("-", "\\"))]
        old_side = [ln for ln in h.lines if not ln.startswith(("+", "\\"))]
        assert len(new_side) == h.new_count
        assert len(old_side) == h.old_count


def test_annotated_line_numbers():
    h = parse_diff(FIXTURE_DIFF)[0].hunks[0]  # @@ -9,6 +9,7 @@
    lines = h.annotated().splitlines()
    assert lines[0] == "@@ -9,6 +9,7 @@"
    assert lines[1].startswith("    9 ")
    assert lines[-1].startswith(f"{h.new_start + h.new_count - 1:>5} ")
    assert "   12 +import warnings" in lines


def test_added_file():
    (f,) = parse_diff(ADDED)
    assert f.status == "added"
    assert f.path == "newfile.py"
    assert f.hunks[0].new_start == 1 and f.hunks[0].new_count == 2
    assert f.hunks[0].old_count == 0


def test_deleted_file():
    (f,) = parse_diff(DELETED)
    assert f.status == "deleted"
    assert f.path == "gone.py"
    assert f.hunks[0].old_count == 1  # "@@ -1 +0,0 @@" - count defaults to 1
    assert f.hunks[0].new_count == 0


def test_renamed_file_no_hunks():
    (f,) = parse_diff(RENAMED)
    assert f.status == "renamed"
    assert f.old_path == "old_name.py" and f.path == "new_name.py"
    assert f.hunks == []


def test_binary_file():
    (f,) = parse_diff(BINARY)
    assert f.is_binary
    assert f.hunks == []


def test_multiple_files():
    files = parse_diff(ADDED + DELETED + BINARY)
    assert [f.path for f in files] == ["newfile.py", "gone.py", "logo.png"]
    assert [f.status for f in files] == ["added", "deleted", "modified"]

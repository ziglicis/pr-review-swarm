"""Parse a unified git diff into per-file, per-hunk structures. Pure functions, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DIFF_GIT = re.compile(r"^diff --git a/(.*?) b/(.*)$")  # misparses paths containing ' b/'
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # trailing context on the @@ line, e.g. " def build_response(...)"
    lines: list[str] = field(default_factory=list)  # raw diff lines: ' ', '+', '-', '\' prefixes

    def annotated(self) -> str:
        """Hunk text with new-file line numbers, so agents can cite real line ranges."""
        out = [f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@{self.header}"]
        new_no = self.new_start
        for line in self.lines:
            if line.startswith(("-", "\\")):
                out.append(f"      {line}")
            else:  # context or added line (exists on the new side)
                out.append(f"{new_no:>5} {line}")
                new_no += 1
        return "\n".join(out)


@dataclass
class FileDiff:
    path: str  # new-side path (equals old path unless renamed)
    old_path: str
    status: str = "modified"  # "added" | "modified" | "deleted" | "renamed"
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    for line in text.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            cur = FileDiff(path=m.group(2), old_path=m.group(1))
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        m = _HUNK.match(line)
        if m:
            old_start, old_count, new_start, new_count, header = m.groups()
            hunk = Hunk(
                old_start=int(old_start),
                old_count=int(old_count or 1),
                new_start=int(new_start),
                new_count=int(new_count or 1),
                header=header,
            )
            cur.hunks.append(hunk)
        elif hunk is not None and (line == "" or line[0] in " +-\\"):
            hunk.lines.append(line)
        elif line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
            cur.path = cur.old_path
        elif line.startswith("rename from"):
            cur.status = "renamed"
        elif line.startswith(("Binary files ", "GIT binary patch")):
            cur.is_binary = True
    return files

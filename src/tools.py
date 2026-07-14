"""Read-only investigation tools agents may call, bound to one PR."""

from __future__ import annotations

import httpx

from src.github_client import GitHubClient, PRData

MAX_FILE_LINES = 1500  # truncate huge files rather than blow the context
MAX_TREE_ENTRIES = 2000

READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read a file from the repository at the PR's head commit. "
        "Use to inspect code outside the diff, e.g. callers of a changed function."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path"},
        },
        "required": ["path"],
    },
}

EXPAND_CONTEXT_TOOL = {
    "name": "expand_context",
    "description": (
        "Read a specific line range of a repository file at the PR's head commit, "
        "with line numbers. Cheaper than read_file for a targeted look."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Repo-relative file path"},
            "line_start": {"type": "integer", "description": "First line (1-based)"},
            "line_end": {"type": "integer", "description": "Last line (inclusive)"},
        },
        "required": ["file", "line_start", "line_end"],
    },
}

LIST_FILES_TOOL = {
    "name": "list_files",
    "description": "List all file paths in the repository at the PR's head commit.",
    "input_schema": {"type": "object", "properties": {}},
}


def numbered(lines: list[str], start: int, end: int) -> str:
    """1-based inclusive slice of file lines with line-number gutters."""
    return "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))


class ToolExecutor:
    """Executes investigation tool calls. Returns (content, is_error); never raises."""

    def __init__(self, client: GitHubClient, pr: PRData):
        self._client = client
        self._pr = pr

    async def execute(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            if name == "read_file":
                return await self._read_file(args["path"]), False
            if name == "expand_context":
                return (
                    await self._expand_context(
                        args["file"], args["line_start"], args["line_end"]
                    ),
                    False,
                )
            if name == "list_files":
                return await self._list_files(), False
            return f"Unknown tool: {name}", True
        except httpx.HTTPStatusError as e:
            return f"GitHub returned {e.response.status_code} — does that path exist?", True
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            return f"Tool call failed: {type(e).__name__}: {e}", True

    async def _fetch(self, path: str) -> str:
        return await self._client.get_file(
            self._pr.owner, self._pr.repo, path, self._pr.head_sha
        )

    async def _read_file(self, path: str) -> str:
        lines = (await self._fetch(path)).splitlines()
        if len(lines) > MAX_FILE_LINES:
            return (
                numbered(lines, 1, MAX_FILE_LINES)
                + f"\n… truncated ({len(lines)} lines total); use expand_context for the rest"
            )
        return numbered(lines, 1, len(lines)) if lines else "(empty file)"

    async def _expand_context(self, file: str, line_start: int, line_end: int) -> str:
        lines = (await self._fetch(file)).splitlines()
        start = max(1, line_start)
        end = min(len(lines), line_end)
        if start > end:
            return f"{file} has {len(lines)} lines; range {line_start}-{line_end} is empty"
        return numbered(lines, start, end)

    async def _list_files(self) -> str:
        paths = await self._client.get_tree(self._pr.owner, self._pr.repo, self._pr.head_sha)
        listing = "\n".join(paths[:MAX_TREE_ENTRIES])
        if len(paths) > MAX_TREE_ENTRIES:
            listing += f"\n… truncated ({len(paths)} files total)"
        return listing

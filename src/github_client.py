"""Read-only GitHub REST client with a per-run cache."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx

API = "https://api.github.com"
MAX_CHANGED_LINES = 1500

_PR_URL = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?(?:[#?].*)?$")


class PRTooLargeError(Exception):
    pass


@dataclass
class PRData:
    owner: str
    repo: str
    number: int
    title: str
    body: str | None
    head_sha: str
    changed_lines: int
    diff: str


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = _PR_URL.match(url.strip())
    if not m:
        raise ValueError(
            f"Not a GitHub PR URL: {url!r} (expected https://github.com/<owner>/<repo>/pull/<n>)"
        )
    owner, repo, number = m.groups()
    return owner, repo, int(number)


class GitHubClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = http or httpx.AsyncClient(base_url=API, headers=headers, timeout=30)
        # per-run cache only, no TTL, no eviction
        self._cache: dict[tuple[str, str], str] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, accept: str) -> str:
        key = (path, accept)
        if key not in self._cache:
            resp = await self._http.get(path, headers={"Accept": accept})
            resp.raise_for_status()
            self._cache[key] = resp.text
        return self._cache[key]

    async def fetch_pr(self, url: str) -> PRData:
        """Fetch metadata + unified diff for a PR. Raises PRTooLargeError over the size cap."""
        owner, repo, number = parse_pr_url(url)
        path = f"/repos/{owner}/{repo}/pulls/{number}"
        meta = json.loads(await self._get(path, "application/vnd.github+json"))
        changed_lines = meta["additions"] + meta["deletions"]
        if changed_lines > MAX_CHANGED_LINES:
            raise PRTooLargeError(
                f"PR changes {changed_lines} lines; the cap is {MAX_CHANGED_LINES}. "
                "Large PRs are out of scope by design."
            )
        diff = await self._get(path, "application/vnd.github.diff")
        return PRData(
            owner=owner,
            repo=repo,
            number=number,
            title=meta["title"],
            body=meta.get("body"),
            head_sha=meta["head"]["sha"],
            changed_lines=changed_lines,
            diff=diff,
        )

    async def get_file(self, owner: str, repo: str, path: str, ref: str) -> str:
        """Raw file contents at a specific commit."""
        return await self._get(
            f"/repos/{owner}/{repo}/contents/{path}?ref={ref}",
            "application/vnd.github.raw+json",
        )

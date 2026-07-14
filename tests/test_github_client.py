import json
from pathlib import Path

import httpx
import pytest

from src.github_client import (
    MAX_CHANGED_LINES,
    GitHubClient,
    PRTooLargeError,
    parse_pr_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
META = (FIXTURES / "pr_metadata.json").read_text()
DIFF = (FIXTURES / "pr.diff").read_text()
PR_URL = "https://github.com/psf/requests/pull/6710"


def make_client(meta: str = META, requests_seen: list | None = None) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_seen is not None:
            requests_seen.append(request)
        if request.url.path == "/repos/psf/requests/pulls/6710":
            if "diff" in request.headers["accept"]:
                return httpx.Response(200, text=DIFF)
            return httpx.Response(200, text=meta)
        if request.url.path.startswith("/repos/psf/requests/contents/"):
            return httpx.Response(200, text="line1\nline2\n")
        if request.url.path.startswith("/repos/psf/requests/git/trees/"):
            return httpx.Response(200, json={
                "tree": [
                    {"path": "src/a.py", "type": "blob"},
                    {"path": "src", "type": "tree"},
                    {"path": "README.md", "type": "blob"},
                ],
                "truncated": False,
            })
        return httpx.Response(404)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    return GitHubClient(http=http)


def test_parse_pr_url():
    assert parse_pr_url(PR_URL) == ("psf", "requests", 6710)
    assert parse_pr_url(PR_URL + "/") == ("psf", "requests", 6710)
    assert parse_pr_url(PR_URL + "#discussion_r1") == ("psf", "requests", 6710)
    assert parse_pr_url("https://github.com/a-b/c.d/pull/1") == ("a-b", "c.d", 1)


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/psf/requests",
        "https://github.com/psf/requests/issues/6710",
        "https://gitlab.com/psf/requests/pull/6710",
        "not a url",
    ],
)
def test_parse_pr_url_rejects(bad):
    with pytest.raises(ValueError):
        parse_pr_url(bad)


async def test_fetch_pr():
    pr = await make_client().fetch_pr(PR_URL)
    assert pr.owner == "psf" and pr.repo == "requests" and pr.number == 6710
    assert pr.title == "Move _get_connection to get_connection_with_tls_context"
    assert pr.head_sha == "92075b330a30b9883f466a43d3f7566ab849f91b"
    assert pr.changed_lines == 37
    assert pr.diff.startswith("diff --git")


async def test_size_cap():
    meta = json.loads(META)
    meta["additions"] = MAX_CHANGED_LINES + 1
    with pytest.raises(PRTooLargeError):
        await make_client(meta=json.dumps(meta)).fetch_pr(PR_URL)


async def test_get_tree_returns_blob_paths_only():
    paths = await make_client().get_tree("psf", "requests", "abc")
    assert paths == ["src/a.py", "README.md"]


async def test_cache_prevents_refetch():
    seen: list = []
    client = make_client(requests_seen=seen)
    ref = "92075b330a30b9883f466a43d3f7566ab849f91b"
    a = await client.get_file("psf", "requests", "src/requests/adapters.py", ref)
    b = await client.get_file("psf", "requests", "src/requests/adapters.py", ref)
    assert a == b == "line1\nline2\n"
    assert len(seen) == 1

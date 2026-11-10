import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fake_cloud import NOW, SUPABASE_KEY, SUPABASE_URL, FakeCloud

from cronchords.__main__ import parse_args, share_once
from cronchords.share import BRANCH, blob_sha, export
from cronchords.supabase import CacheEntry, Source, VideoCacheEntry

REPOSITORY = "someone/cronchords"
TOKEN = "ghs_test"


@dataclass
class FakeGitHub:
    """A repository's git objects and branches, behind the GitHub API."""

    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=lambda: {"refs/heads/main": "main-commit"})
    writes: list[httpx.Request] = field(default_factory=list)
    can_write: bool = True

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = request.url.path.removeprefix(f"/repos/{REPOSITORY}")
        body = json.loads(request.content) if request.content else None
        if request.method != "GET":
            self.writes.append(request)
            if not self.can_write:
                return httpx.Response(403, json={"message": "Resource not accessible"})

        if request.method == "GET" and path.startswith("/git/trees/"):
            branch = path.removeprefix("/git/trees/")
            if f"refs/heads/{branch}" not in self.refs:
                return httpx.Response(404, json={"message": "Not Found"})
            tree = self.objects[self.commit(branch)["tree"]]
            entries = [
                {"path": e["path"], "sha": blob_sha(self.objects[e["sha"]]["content"])}
                for e in tree["entries"]
            ]
            return httpx.Response(200, json={"tree": entries})
        if path == "/git/blobs":
            assert body["encoding"] == "utf-8"
            return self.store({"type": "blob", "content": body["content"].encode()})
        if path == "/git/trees":
            assert "base_tree" not in body
            return self.store({"type": "tree", "entries": body["tree"]})
        if path == "/git/commits":
            return self.store({"type": "commit", "tree": body["tree"], "parents": body["parents"]})
        if request.method == "POST" and path == "/git/refs":
            assert body["ref"] not in self.refs
            self.refs[body["ref"]] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})
        if request.method == "PATCH" and path.startswith("/git/refs/"):
            ref = "refs/" + path.removeprefix("/git/refs/")
            assert ref in self.refs and body["force"]
            self.refs[ref] = body["sha"]
            return httpx.Response(200, json={"ref": ref})
        raise AssertionError(f"unexpected GitHub call {request.method} {request.url}")

    def store(self, obj: dict[str, Any]) -> httpx.Response:
        sha = f"{obj['type']}-{len(self.objects) + 1}"
        self.objects[sha] = obj
        return httpx.Response(201, json={"sha": sha})

    def commit(self, branch: str) -> dict[str, Any]:
        return self.objects[self.refs[f"refs/heads/{branch}"]]

    def file(self, branch: str, path: str) -> bytes | None:
        if f"refs/heads/{branch}" not in self.refs:
            return None
        tree = self.objects[self.commit(branch)["tree"]]
        entries = [entry for entry in tree["entries"] if entry["path"] == path]
        return self.objects[entries[0]["sha"]]["content"] if entries else None


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    cloud.cache["GBDUW0000059"] = {
        "isrc": "GBDUW0000059",
        "spotify_track_id": "lucky",
        "youtube_video_id": "lucky-video",
        "searched_at": NOW.isoformat(),
    }
    # Searched, but without a match.
    cloud.cache["USWB10400047"] = {
        "isrc": "USWB10400047",
        "spotify_track_id": "dreams",
        "youtube_video_id": None,
        "searched_at": NOW.isoformat(),
    }
    cloud.video_cache["dreams-video"] = {
        "youtube_video_id": "dreams-video",
        "spotify_track_id": "dreams",
        "isrc": "USWB10400047",
        "searched_at": NOW.isoformat(),
        "source": "correction",
    }
    return cloud


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


def share(cloud: FakeCloud, github: FakeGitHub) -> int:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return github.handle(request)
        return cloud.handle(request)

    transport = httpx.MockTransport(handler)
    return share_once(SUPABASE_URL, SUPABASE_KEY, REPOSITORY, TOKEN, transport=transport)


def shared_pairs(github: FakeGitHub) -> list[dict[str, str]]:
    document = json.loads(github.file(BRANCH, "pairs.json"))
    assert document["version"] == 1
    return document["pairs"]


def test_the_first_share_creates_a_branch_with_only_the_matches(cloud, github):
    assert share(cloud, github) == 0

    commit = github.commit(BRANCH)
    assert commit["parents"] == []
    assert [entry["path"] for entry in github.objects[commit["tree"]]["entries"]] == ["pairs.json"]
    assert shared_pairs(github) == [
        {
            "track": "dreams",
            "video": "dreams-video",
            "isrc": "USWB10400047",
            "source": "correction",
        },
        {"track": "lucky", "video": "lucky-video", "isrc": "GBDUW0000059", "source": "search"},
    ]
    assert github.refs["refs/heads/main"] == "main-commit"


def test_matches_that_havent_changed_are_not_shared_again(cloud, github):
    share(cloud, github)
    github.writes.clear()

    assert share(cloud, github) == 0

    assert github.writes == []


def test_changed_matches_replace_the_branchs_only_commit(cloud, github):
    share(cloud, github)
    cloud.cache["USWB10400047"] |= {"youtube_video_id": "dreams-video"}

    assert share(cloud, github) == 0

    assert github.commit(BRANCH)["parents"] == []
    [dreams] = [pair for pair in shared_pairs(github) if pair["track"] == "dreams"]
    # The same pair from both caches, and the video's entry was corrected by hand.
    assert dreams["source"] == "correction"
    assert len(shared_pairs(github)) == 2


def test_a_token_that_cant_write_is_reported(cloud, github, caplog):
    github.can_write = False

    assert share(cloud, github) == 1

    assert "needs write access to the repository's contents" in caplog.records[-1].getMessage()
    assert f"refs/heads/{BRANCH}" not in github.refs


def test_the_export_is_the_same_for_the_same_matches():
    searched = NOW - timedelta(days=3)
    entries = [
        CacheEntry("ISRC2", "t2", "v2", searched),
        CacheEntry("ISRC1", "t1", "v1", NOW),
        CacheEntry("ISRC3", "t3", None, NOW),
    ]
    video_entries = [VideoCacheEntry("v4", "t4", None, NOW, Source.CORRECTION)]

    exported = export(entries, video_entries)

    assert exported == export(reversed(entries), video_entries)
    assert exported == (
        b'{"pairs":[{"isrc":"ISRC1","source":"search","track":"t1","video":"v1"},'
        b'{"isrc":"ISRC2","source":"search","track":"t2","video":"v2"},'
        b'{"source":"correction","track":"t4","video":"v4"}],"version":1}'
    )


def test_matches_taken_from_the_shared_list_are_not_shared_again():
    entries = [
        CacheEntry("ISRC1", "t1", "v1", NOW, Source.SHARED),
        CacheEntry("ISRC2", "t2", "v2", NOW),
    ]
    video_entries = [VideoCacheEntry("v1", "t1", "ISRC1", NOW, Source.SHARED)]

    pairs = json.loads(export(entries, video_entries))["pairs"]

    assert [pair["track"] for pair in pairs] == ["t2"]


def test_blob_sha_is_the_one_git_gives():
    # printf 'hello\n' | git hash-object --stdin
    assert blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_share_doesnt_go_with_every():
    assert parse_args(["--share"]).share
    with pytest.raises(SystemExit):
        parse_args(["--share", "--every", "60"])

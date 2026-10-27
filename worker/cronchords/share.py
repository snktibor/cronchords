"""Shares the matches found so far, for a cache that every cronchords user can use.

Sharing is opt-in, see "Sharing matches" in the README. The matches go to
pairs.json, the only file on the cronchords-export branch of the repository
the worker runs from, where the shared cache collects them. The branch has no
history in common with the code: every share replaces its single commit, and
only when the matches have changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from typing import Any

import httpx

from .http import send
from .supabase import CacheEntry, Database, Source, VideoCacheEntry

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
BRANCH = "cronchords-export"
FILE = "pairs.json"
FORMAT_VERSION = 1


class ShareError(Exception):
    pass


def share(db: Database, http: httpx.Client, repository: str, token: str) -> None:
    content = export(db.get_matched_cache_entries(), db.get_matched_video_cache_entries())
    pairs = json.loads(content)["pairs"]
    corrections = sum(pair["source"] == Source.CORRECTION for pair in pairs)
    if Repository(http, repository, token).publish(content):
        log.info(
            "Shared %d matches, %d of them corrected by hand, on the %s branch of %s",
            len(pairs),
            corrections,
            BRANCH,
            repository,
        )
    else:
        log.info("The %d shared matches are up to date", len(pairs))


def export(entries: Iterable[CacheEntry], video_entries: Iterable[VideoCacheEntry]) -> bytes:
    """The matches the way pairs.json holds them, the same bytes for the same matches."""
    found = [
        *((e.spotify_track_id, e.youtube_video_id, e.isrc, e.source) for e in entries),
        *((e.spotify_track_id, e.youtube_video_id, e.isrc, e.source) for e in video_entries),
    ]
    pairs: dict[tuple[str, str], dict[str, str]] = {}
    for track_id, video_id, isrc, source in found:
        if not track_id or not video_id:
            continue
        pair = pairs.setdefault(
            (track_id, video_id), {"track": track_id, "video": video_id, "source": source}
        )
        if isrc:
            pair["isrc"] = isrc
        # Both caches can hold the pair, and a person may have corrected either.
        if source is Source.CORRECTION:
            pair["source"] = source
    document = {"version": FORMAT_VERSION, "pairs": [pairs[key] for key in sorted(pairs)]}
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


class Repository:
    """The few GitHub API calls it takes to put a file on a branch of its own."""

    def __init__(self, http: httpx.Client, name: str, token: str):
        self._http = http
        self.name = name
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def publish(self, content: bytes) -> bool:
        """Make content the export branch's only file, unless it is already. True if it changed."""
        # Trees can be looked up by branch name. Unlike the contents API, the
        # tree doesn't send the file along, which only works up to 1 MB anyway.
        branch = self._call("GET", f"/git/trees/{BRANCH}", missing_ok=True)
        sha = blob_sha(content)
        if branch and any(e["path"] == FILE and e["sha"] == sha for e in branch["tree"]):
            return False

        blob = self._call(
            "POST", "/git/blobs", json={"content": content.decode(), "encoding": "utf-8"}
        )
        tree = self._call(
            "POST",
            "/git/trees",
            json={"tree": [{"path": FILE, "mode": "100644", "type": "blob", "sha": blob["sha"]}]},
        )
        commit = self._call(
            "POST",
            "/git/commits",
            json={"message": "Share the matches found so far", "tree": tree["sha"], "parents": []},
        )
        if branch:
            self._call(
                "PATCH", f"/git/refs/heads/{BRANCH}", json={"sha": commit["sha"], "force": True}
            )
        else:
            self._call(
                "POST", "/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]}
            )
        return True

    def _call(
        self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any
    ) -> dict[str, Any] | None:
        response = send(
            self._http,
            method,
            f"{API_URL}/repos/{self.name}{path}",
            headers=self._headers,
            **kwargs,
        )
        if response.is_success:
            return response.json()
        if response.status_code == 404 and missing_ok:
            return None

        message = _api_error(response)
        if response.status_code == 403:
            raise ShareError(
                f"GitHub didn't allow writing to {self.name} ({message}). The token needs "
                "write access to the repository's contents."
            )
        raise ShareError(f"GitHub API error {response.status_code}: {message}")


def blob_sha(content: bytes) -> str:
    """The id git gives a file with this content, which the GitHub API reports as its sha."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def _api_error(response: httpx.Response) -> str:
    try:
        return response.json()["message"]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]

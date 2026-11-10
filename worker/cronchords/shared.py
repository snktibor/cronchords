"""Looks songs up in youtube-spotify-mapper, the list cronchords users share matches to.

A song that isn't in the cache yet is looked up there before it's searched for:
a YouTube search costs 100 units of the daily quota, looking up the videos the
list names costs 1. The list is only a hint, though. sync.py checks each pair
with the matching rules before it takes it, so that a wrong pair someone shared
doesn't get in, and it doesn't share the pairs it took from the list again, see
share.py. Reading the list is on unless USE_SHARED_MATCHES is false.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .http import send

log = logging.getLogger(__name__)

DATA_URL = "https://raw.githubusercontent.com/snktibor/youtube-spotify-mapper/main/data"
DATA_FORMAT = 1

# The list says why it has a pair: "reviewed" and "corrected" ones a person
# picked, "matched" ones passed the matching rules.
SOURCES = frozenset({"reviewed", "corrected", "matched"})

# Ids go into request URLs, so anything else is left out.
ID = re.compile(r"[0-9A-Za-z_-]{1,64}")


@dataclass(frozen=True)
class Hint:
    """A pair the shared list has for a song, still to be checked."""

    track_id: str
    video_id: str
    # A person picked it, so it may not pass the matching rules, see sync.py.
    picked: bool


class SharedList:
    """Reads the list's files as they are needed, each one once per run.

    If the list can't be read, the run goes on without it.
    """

    def __init__(self, http: httpx.Client, url: str = DATA_URL):
        self._http = http
        self._url = url
        self._files: dict[str, dict[str, Any]] = {}
        self._usable: bool | None = None

    def videos(self, isrc: str) -> list[Hint]:
        """The videos the list has for an ISRC, best first."""
        return [
            Hint(row["track"], row["video"], row["source"] != "matched")
            for row in self._rows("isrc", isrc)
            if _valid(row.get("video"))
        ]

    def tracks(self, video_id: str) -> list[Hint]:
        """The Spotify tracks the list has for a video, best first."""
        return [
            Hint(row["track"], video_id, row["source"] != "matched")
            for row in self._rows("video", video_id)
        ]

    def _rows(self, folder: str, key: str) -> list[dict[str, Any]]:
        if self._usable is None:
            index = self._fetch("index.json")
            self._usable = index is not None and index.get("version") == DATA_FORMAT
            if index is not None and not self._usable:
                log.warning(
                    "The shared list of matches has a format this version of cronchords "
                    "doesn't know. Update cronchords to use it again."
                )
        if not self._usable:
            return []
        path = f"{folder}/{shard(key)}.json"
        if path not in self._files:
            content = self._fetch(path, missing_ok=True)
            if content is None:
                self._usable = False
                return []
            self._files[path] = content
        rows = self._files[path].get(key)
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if isinstance(row, dict) and _valid(row.get("track")) and row.get("source") in SOURCES
        ]

    def _fetch(self, path: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        """A file of the list, {} if it isn't there, None if the list can't be read."""
        try:
            response = send(self._http, "GET", f"{self._url}/{path}")
        except httpx.TransportError as exc:
            problem = str(exc)
        else:
            if response.status_code == 404 and missing_ok:
                # No pairs in this file yet.
                return {}
            if response.is_success:
                try:
                    content = response.json()
                except ValueError:
                    content = None
                if isinstance(content, dict):
                    return content
                problem = "it isn't JSON"
            else:
                problem = f"HTTP {response.status_code}"
        log.warning(
            "The shared list of matches can't be read (%s), songs are searched for instead",
            problem,
        )
        return None


def shard(key: str) -> str:
    """The file a key is in, the way youtube-spotify-mapper splits them up."""
    return hashlib.sha1(key.encode()).hexdigest()[:2]


def _valid(value: Any) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None

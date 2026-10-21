"""In-memory stand-ins for Supabase, Spotify and YouTube behind an httpx.MockTransport."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx

from cronchords.__main__ import run
from cronchords.supabase import Database

SUPABASE_URL = "https://project.supabase.co"
SUPABASE_KEY = "sb_secret_test"

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def spotify_entry(
    isrc: str | None,
    name: str,
    artist: str,
    duration_ms: int = 200_000,
    track_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A playlist entry the way /playlists/{id}/items returns it."""
    return {
        "is_local": False,
        "item": {
            "type": "track",
            "id": track_id or f"sp-{name.lower().replace(' ', '-')}",
            "name": name,
            "duration_ms": duration_ms,
            "external_ids": {"isrc": isrc} if isrc else {},
            "artists": [{"name": artist}],
        },
        **extra,
    }


@dataclass
class FakeVideo:
    title: str
    channel: str
    duration: str = "PT3M20S"
    removed: bool = False


@dataclass
class FakeCloud:
    config: dict[str, Any] = field(
        default_factory=lambda: {
            "id": 1,
            "spotify_client_id": "spotify-client",
            "spotify_refresh_token": "spotify-refresh-1",
            "spotify_connected_at": "2026-10-01T00:00:00+00:00",
            "google_client_id": "google-client",
            "google_client_secret": "google-secret",
            "youtube_refresh_token": "youtube-refresh",
            "youtube_connected_at": "2026-10-01T00:00:00+00:00",
        }
    )
    mappings: list[dict[str, Any]] = field(default_factory=list)
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    video_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Spotify playlist id -> {"snapshot_id": ..., "entries": [...]}
    spotify_playlists: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Spotify track id -> track object, for search results and adding by URI
    spotify_catalog: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Spotify search query -> track ids, best first
    spotify_search_results: dict[str, list[str]] = field(default_factory=dict)
    # False: the token lacks the playlist-modify scopes
    spotify_can_edit: bool = True
    spotify_rate_limited: bool = False
    # Called after a page of a Spotify playlist was served, to edit it mid-read
    while_reading_spotify: Callable[[], None] | None = None
    # Videos the next YouTube playlist listing skips, as paging does when the
    # playlist changes meanwhile
    skip_once: set[str] = field(default_factory=set)
    # YouTube playlist id -> video ids in playlist order
    youtube_playlists: dict[str, list[str]] = field(default_factory=dict)
    videos: dict[str, FakeVideo] = field(default_factory=dict)
    # search query -> video ids, best first
    search_results: dict[str, list[str]] = field(default_factory=dict)
    searches_left: int = 100
    units_left: int = 10_000
    spotify_rotations: int = 1
    requests: list[httpx.Request] = field(default_factory=list)
    # The time of the current sync()
    now: datetime = NOW
    # When things were added to playlists, by (playlist id, track or video id).
    # What the tests put in counts as added when the worker first lists it.
    added: dict[tuple[str, str], datetime] = field(default_factory=dict)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_mapping(
        self,
        spotify_id: str,
        youtube_id: str,
        direction: str = "spotify_to_youtube",
        **fields: Any,
    ) -> dict[str, Any]:
        mapping = {
            "id": f"mapping-{len(self.mappings) + 1}",
            "spotify_playlist_id": spotify_id,
            "spotify_playlist_name": spotify_id.title(),
            "youtube_playlist_id": youtube_id,
            "direction": direction,
            "spotify_snapshot_id": None,
            "youtube_snapshot_id": None,
            "synced_songs": [],
            "last_synced_at": None,
            "last_error": None,
            **fields,
        }
        self.mappings.append(mapping)
        self.youtube_playlists.setdefault(youtube_id, [])
        return mapping

    def add_video(
        self, query: str, video_id: str, title: str, channel: str, duration: str = "PT3M20S"
    ) -> None:
        self.videos[video_id] = FakeVideo(title, channel, duration)
        self.search_results.setdefault(query, []).append(video_id)

    def add_track(self, query: str, entry: dict[str, Any]) -> None:
        """Make a track findable with a Spotify search, and addable to playlists."""
        self.spotify_catalog[entry["item"]["id"]] = entry["item"]
        self.spotify_search_results.setdefault(query, []).append(entry["item"]["id"])

    def spotify_track_ids(self, playlist_id: str) -> list[str]:
        return [entry["item"]["id"] for entry in self.spotify_playlists[playlist_id]["entries"]]

    def youtube_searches(self) -> list[str]:
        return [r.url.params["q"] for r in self.calls("www.googleapis.com", "/youtube/v3/search")]

    def spotify_searches(self) -> list[str]:
        return [r.url.params["q"] for r in self.calls("api.spotify.com", "/v1/search")]

    def calls(self, host: str, path_prefix: str = "") -> list[httpx.Request]:
        return [
            r for r in self.requests if r.url.host == host and r.url.path.startswith(path_prefix)
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "project.supabase.co":
            return self._supabase(request)
        if host == "accounts.spotify.com":
            return self._spotify_token(request)
        if host == "api.spotify.com":
            return self._spotify_api(request)
        if host == "oauth2.googleapis.com":
            return self._google_token(request)
        if host == "www.googleapis.com":
            return self._youtube(request)
        raise AssertionError(f"unexpected request to {request.url}")

    # Supabase (PostgREST)

    def _supabase(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("apikey") != SUPABASE_KEY:
            return httpx.Response(401, json={"message": "Invalid API key"})
        table = request.url.path.removeprefix("/rest/v1/")
        params = dict(request.url.params)
        body = json.loads(request.content) if request.content else None

        if table == "app_config":
            if request.method == "GET":
                return httpx.Response(200, json=[self.config] if self.config else [])
            self.config.update(body)
            return httpx.Response(204)

        if table == "playlist_mappings":
            if request.method == "GET":
                columns = params["select"].split(",")
                return httpx.Response(200, json=[{c: m[c] for c in columns} for m in self.mappings])
            mapping_id = params["id"].removeprefix("eq.")
            for mapping in self.mappings:
                if mapping["id"] == mapping_id:
                    mapping.update(body)
            return httpx.Response(204)

        if table in ("track_sync_cache", "video_sync_cache"):
            rows, key = (
                (self.cache, "isrc")
                if table == "track_sync_cache"
                else (self.video_cache, "youtube_video_id")
            )
            if request.method == "GET":
                [(column, condition)] = [(k, v) for k, v in params.items() if k != "select"]
                wanted = re.fullmatch(r"in\.\((.*)\)", condition).group(1).split(",")
                return httpx.Response(
                    200, json=[row for row in rows.values() if row[column] in wanted]
                )
            if request.method == "POST":
                prefer = request.headers["Prefer"]
                if "merge-duplicates" in prefer or body[key] not in rows:
                    rows[body[key]] = body
                else:
                    assert "ignore-duplicates" in prefer
                return httpx.Response(201)

        raise AssertionError(f"unexpected Supabase call {request.method} {request.url}")

    # Spotify

    def _spotify_token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["client_id"] == "spotify-client"
        assert form["grant_type"] == "refresh_token"
        if form["refresh_token"] != f"spotify-refresh-{self.spotify_rotations}":
            return httpx.Response(400, json={"error": "invalid_grant"})
        # PKCE refresh tokens are single use: every refresh hands out a new one.
        self.spotify_rotations += 1
        return httpx.Response(
            200,
            json={
                "access_token": "spotify-access",
                "refresh_token": f"spotify-refresh-{self.spotify_rotations}",
                "expires_in": 3600,
            },
        )

    def _spotify_api(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer spotify-access"
        if self.spotify_rate_limited:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        if request.url.path == "/v1/search":
            assert request.url.params["type"] == "track"
            limit = int(request.url.params["limit"])
            assert limit <= 10
            ids = self.spotify_search_results.get(request.url.params["q"], [])[:limit]
            return httpx.Response(
                200, json={"tracks": {"items": [self.spotify_catalog[i] for i in ids]}}
            )

        match = re.fullmatch(r"/v1/playlists/([^/]+)(/items)?", request.url.path)
        playlist = self.spotify_playlists.get(match.group(1)) if match else None
        if playlist is None:
            return _spotify_error(404, "Not found")
        if not match.group(2):
            return httpx.Response(200, json={"snapshot_id": playlist["snapshot_id"]})

        if request.method in ("POST", "DELETE"):
            if not self.spotify_can_edit:
                return _spotify_error(403, "Insufficient client scope")
            body = json.loads(request.content)
            if request.method == "POST":
                assert len(body["uris"]) <= 100
                track_ids = [uri.removeprefix("spotify:track:") for uri in body["uris"]]
                playlist["entries"] += [
                    {"is_local": False, "item": self.spotify_catalog[track_id]}
                    for track_id in track_ids
                ]
                for track_id in track_ids:
                    self.added[match.group(1), track_id] = self.now
            else:
                assert len(body["items"]) <= 100
                uris = {item["uri"] for item in body["items"]}
                playlist["entries"] = [
                    entry
                    for entry in playlist["entries"]
                    if f"spotify:track:{entry['item']['id']}" not in uris
                ]
            playlist["snapshot_id"] += "+"
            status = 201 if request.method == "POST" else 200
            return httpx.Response(status, json={"snapshot_id": playlist["snapshot_id"]})

        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        entries = playlist["entries"]
        has_more = offset + limit < len(entries)
        page = {
            "total": len(entries),
            "items": [
                {
                    "added_at": self._added_at(match.group(1), (entry["item"] or {}).get("id")),
                    **entry,
                }
                for entry in entries[offset : offset + limit]
            ],
            "next": f"{request.url}&more" if has_more else None,
        }
        if self.while_reading_spotify:
            self.while_reading_spotify()
        return httpx.Response(200, json=page)

    # Google / YouTube

    def _google_token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form["refresh_token"] != "youtube-refresh" or form["client_secret"] != "google-secret":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "youtube-access", "expires_in": 3599})

    def _youtube(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer youtube-access"
        resource = request.url.path.removeprefix("/youtube/v3/")
        params = request.url.params

        if resource == "search":
            if self.searches_left <= 0:
                return _youtube_error(403, "quotaExceeded")
            self.searches_left -= 1
            ids = self.search_results.get(params["q"], [])[: int(params["maxResults"])]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": {"videoId": vid},
                            "snippet": {
                                "title": self.videos[vid].title.replace("'", "&#39;"),
                                "channelTitle": self.videos[vid].channel,
                            },
                        }
                        for vid in ids
                    ]
                },
            )

        cost = 1 if request.method == "GET" else 50
        if self.units_left < cost:
            return _youtube_error(403, "quotaExceeded")
        self.units_left -= cost

        if resource == "videos":
            ids = params["id"].split(",")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": vid, "contentDetails": {"duration": self.videos[vid].duration}}
                        for vid in ids
                        if vid in self.videos and not self.videos[vid].removed
                    ]
                },
            )

        if resource == "playlistItems" and request.method == "GET":
            playlist_id = params["playlistId"]
            playlist = self.youtube_playlists.get(playlist_id)
            if playlist is None:
                return _youtube_error(404, "playlistNotFound")
            if self.skip_once:
                playlist = [vid for vid in playlist if vid not in self.skip_once]
                self.skip_once = set()
            start = int(params.get("pageToken", 0))
            end = start + int(params["maxResults"])
            page: dict[str, Any] = {
                "items": [self._playlist_item(playlist_id, vid) for vid in playlist[start:end]]
            }
            if end < len(playlist):
                page["nextPageToken"] = str(end)
            return httpx.Response(200, json=page)

        if resource == "playlistItems" and request.method == "POST":
            snippet = json.loads(request.content)["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            video = self.videos.get(video_id)
            if video is None or video.removed:
                return _youtube_error(404, "videoNotFound")
            self.youtube_playlists[snippet["playlistId"]].append(video_id)
            self.added[snippet["playlistId"], video_id] = self.now
            return httpx.Response(200, json={"id": f"{snippet['playlistId']}/{video_id}"})

        if resource == "playlistItems" and request.method == "DELETE":
            playlist_id, video_id = params["id"].split("/")
            self.youtube_playlists[playlist_id].remove(video_id)
            return httpx.Response(204)

        raise AssertionError(f"unexpected YouTube call {request.method} {request.url}")

    def _playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]:
        snippet: dict[str, Any] = {
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
            "publishedAt": self._added_at(playlist_id, video_id),
        }
        video = self.videos.get(video_id)
        if video is None:
            snippet |= {"title": f"Video {video_id}", "videoOwnerChannelTitle": "Someone"}
        elif video.removed:
            snippet["title"] = "Deleted video"
        else:
            snippet |= {"title": video.title, "videoOwnerChannelTitle": video.channel}
        # Ids of playlist items are opaque strings; these ones say what to remove.
        return {"id": f"{playlist_id}/{video_id}", "snippet": snippet}

    def _added_at(self, playlist_id: str, item_id: str | None) -> str:
        added = self.added.setdefault((playlist_id, item_id or ""), self.now)
        return added.strftime("%Y-%m-%dT%H:%M:%SZ")


def _spotify_error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"status": status, "message": message}})


def _youtube_error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": f"The request failed: <a href='/docs'>{reason}</a>.",
                "errors": [{"reason": reason, "domain": "youtube"}],
            }
        },
    )


def sync(cloud: FakeCloud, now: datetime = NOW) -> int:
    """Run the worker once against the fake cloud, return its exit code."""
    cloud.now = now
    db = Database(SUPABASE_URL, SUPABASE_KEY, transport=cloud.transport)
    try:
        with httpx.Client(transport=cloud.transport) as http:
            return run(db, http, now=lambda: now)
    finally:
        db.close()

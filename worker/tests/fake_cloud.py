"""In-memory stand-ins for Supabase, Spotify and YouTube behind an httpx.MockTransport."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

SUPABASE_URL = "https://project.supabase.co"
SUPABASE_KEY = "sb_secret_test"


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
    # Spotify playlist id -> {"snapshot_id": ..., "entries": [...]}
    spotify_playlists: dict[str, dict[str, Any]] = field(default_factory=dict)
    # YouTube playlist id -> video ids in playlist order
    youtube_playlists: dict[str, list[str]] = field(default_factory=dict)
    videos: dict[str, FakeVideo] = field(default_factory=dict)
    # search query -> video ids, best first
    search_results: dict[str, list[str]] = field(default_factory=dict)
    searches_left: int = 100
    units_left: int = 10_000
    spotify_rotations: int = 1
    requests: list[httpx.Request] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_mapping(self, spotify_id: str, youtube_id: str, **fields: Any) -> dict[str, Any]:
        mapping = {
            "id": f"mapping-{len(self.mappings) + 1}",
            "spotify_playlist_id": spotify_id,
            "spotify_playlist_name": spotify_id.title(),
            "youtube_playlist_id": youtube_id,
            "spotify_snapshot_id": None,
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

        if table == "track_sync_cache":
            if request.method == "GET":
                isrcs = re.fullmatch(r"in\.\((.*)\)", params["isrc"]).group(1).split(",")
                return httpx.Response(200, json=[self.cache[i] for i in isrcs if i in self.cache])
            if request.method == "POST":
                assert "merge-duplicates" in request.headers["Prefer"]
                self.cache[body["isrc"]] = body
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
        match = re.fullmatch(r"/v1/playlists/([^/]+)(/items)?", request.url.path)
        playlist = self.spotify_playlists.get(match.group(1)) if match else None
        if playlist is None:
            return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
        if not match.group(2):
            return httpx.Response(200, json={"snapshot_id": playlist["snapshot_id"]})
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        entries = playlist["entries"]
        has_more = offset + limit < len(entries)
        return httpx.Response(
            200,
            json={
                "items": entries[offset : offset + limit],
                "next": f"{request.url}&more" if has_more else None,
            },
        )

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
            return httpx.Response(200, json={"id": f"{snippet['playlistId']}/{video_id}"})

        if resource == "playlistItems" and request.method == "DELETE":
            playlist_id, video_id = params["id"].split("/")
            self.youtube_playlists[playlist_id].remove(video_id)
            return httpx.Response(204)

        raise AssertionError(f"unexpected YouTube call {request.method} {request.url}")

    def _playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]:
        snippet: dict[str, Any] = {"resourceId": {"kind": "youtube#video", "videoId": video_id}}
        video = self.videos.get(video_id)
        if video is None:
            snippet |= {"title": f"Video {video_id}", "videoOwnerChannelTitle": "Someone"}
        elif video.removed:
            snippet["title"] = "Deleted video"
        else:
            snippet |= {"title": video.title, "videoOwnerChannelTitle": video.channel}
        # Ids of playlist items are opaque strings; these ones say what to remove.
        return {"id": f"{playlist_id}/{video_id}", "snippet": snippet}


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

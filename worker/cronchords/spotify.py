"""The parts of the Spotify Web API the sync needs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from .http import send

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"

# Since the February 2026 API changes playlist entries live under /items and
# each entry holds its track in "item" (it used to be "track").
ITEM_FIELDS = (
    "total,next,items(is_local,item(type,id,name,duration_ms,external_ids(isrc),artists(name)))"
)
PAGE_SIZE = 50
# Adding and removing takes at most 100 tracks per request.
WRITE_BATCH = 100
# Also since February 2026, search returns at most 10 results.
SEARCH_LIMIT = 10


class SpotifyError(Exception):
    pass


class SpotifyAuthError(SpotifyError):
    """The stored refresh token stopped working, Spotify has to be reconnected."""


class SpotifyRateLimitError(SpotifyError):
    """Spotify wants the app to back off for longer than a run should wait."""


class PlaylistChangedError(SpotifyError):
    """The playlist changed while it was being read, so the read can't be trusted."""


@dataclass(frozen=True)
class Track:
    id: str
    isrc: str | None
    name: str
    artists: tuple[str, ...]
    duration_ms: int

    def __str__(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None


def refresh_access_token(http: httpx.Client, client_id: str, refresh_token: str) -> TokenGrant:
    """Trade the refresh token for an access token (PKCE apps have no client secret).

    Spotify rotates refresh tokens: when the response carries a new one, the
    caller must store it, because the old one stops working.
    """
    response = send(
        http,
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )
    if response.is_success:
        body = response.json()
        return TokenGrant(body["access_token"], body.get("refresh_token"))

    error = _oauth_error(response)
    if error == "invalid_grant":
        raise SpotifyAuthError(
            "Spotify rejected the stored refresh token (it expires 6 months after connecting, "
            "or it was revoked). Reconnect Spotify in the dashboard."
        )
    if error == "invalid_client":
        raise SpotifyAuthError("Spotify doesn't know the client ID. Check it in the dashboard.")
    raise SpotifyError(
        f"Spotify token request failed ({response.status_code}): {error or response.text[:200]}"
    )


class SpotifyClient:
    def __init__(self, http: httpx.Client, access_token: str):
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self.searches = 0

    def get_snapshot_id(self, playlist_id: str) -> str:
        playlist = self._call("GET", f"/playlists/{playlist_id}", params={"fields": "snapshot_id"})
        return playlist["snapshot_id"]

    def get_tracks(self, playlist_id: str, snapshot_id: str) -> tuple[list[Track], int]:
        """Return the tracks of the playlist at snapshot_id, and how many entries were skipped.

        Local files and podcast episodes can't be synced, so they are left out.
        Raises PlaylistChangedError if the playlist moved on from snapshot_id:
        paging through a playlist that changes meanwhile can skip entries, and
        a two-way sync would take a skipped song for a removed one.
        """
        tracks: list[Track] = []
        skipped = 0
        offset = 0
        while True:
            page = self._call(
                "GET",
                f"/playlists/{playlist_id}/items",
                params={"fields": ITEM_FIELDS, "limit": PAGE_SIZE, "offset": offset},
            )
            for entry in page.get("items") or []:
                track = None if entry.get("is_local") else _parse_track(entry.get("item"))
                if track:
                    tracks.append(track)
                else:
                    skipped += 1
            if not page.get("next"):
                break
            offset += PAGE_SIZE

        if (
            page.get("total") not in (None, len(tracks) + skipped)
            or self.get_snapshot_id(playlist_id) != snapshot_id
        ):
            raise PlaylistChangedError("The Spotify playlist changed while it was being read")
        return tracks, skipped

    def search(self, query: str) -> list[Track]:
        self.searches += 1
        body = self._call(
            "GET", "/search", params={"q": query, "type": "track", "limit": SEARCH_LIMIT}
        )
        items = (body.get("tracks") or {}).get("items") or []
        return [track for track in map(_parse_track, items) if track]

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        for batch in _batches(track_ids):
            uris = [f"spotify:track:{track_id}" for track_id in batch]
            self._call("POST", f"/playlists/{playlist_id}/items", json={"uris": uris})

    def remove_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Remove every occurrence of the tracks from the playlist."""
        for batch in _batches(track_ids):
            items = [{"uri": f"spotify:track:{track_id}"} for track_id in batch]
            self._call("DELETE", f"/playlists/{playlist_id}/items", json={"items": items})

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = send(self._http, method, f"{API_URL}{path}", headers=self._headers, **kwargs)
        if response.is_success:
            return response.json() if response.content else {}

        message = _api_error(response)
        if response.status_code == 429:
            raise SpotifyRateLimitError(
                "Spotify is limiting requests for a while, the sync continues in a later run."
            )
        if path.startswith("/playlists/"):
            if response.status_code == 404 or (response.status_code == 403 and method == "GET"):
                raise SpotifyError(
                    "Spotify playlist not found or not accessible. Only playlists you own "
                    "or collaborate on can be synced."
                )
            if response.status_code == 403:
                raise SpotifyError(
                    f"Spotify didn't allow changing the playlist ({message}). Reconnect Spotify "
                    "in the dashboard so that cronchords may edit your playlists."
                )
        raise SpotifyError(f"Spotify API error {response.status_code}: {message}")


def normalize_isrc(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _parse_track(item: Any) -> Track | None:
    if not isinstance(item, dict) or item.get("type") != "track" or not item.get("id"):
        return None
    return Track(
        id=item["id"],
        isrc=normalize_isrc((item.get("external_ids") or {}).get("isrc")) or None,
        name=item.get("name") or "",
        artists=tuple(a["name"] for a in item.get("artists") or [] if a.get("name")),
        duration_ms=item.get("duration_ms") or 0,
    )


def _batches(track_ids: list[str]) -> list[list[str]]:
    return [track_ids[i : i + WRITE_BATCH] for i in range(0, len(track_ids), WRITE_BATCH)]


def _api_error(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error")
    except (ValueError, AttributeError):
        return None

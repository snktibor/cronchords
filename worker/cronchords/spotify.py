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
ITEM_FIELDS = "next,items(is_local,item(type,id,name,duration_ms,external_ids(isrc),artists(name)))"
PAGE_SIZE = 50


class SpotifyError(Exception):
    pass


class SpotifyAuthError(SpotifyError):
    """The stored refresh token stopped working, Spotify has to be reconnected."""


@dataclass(frozen=True)
class Track:
    id: str
    isrc: str
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

    def get_snapshot_id(self, playlist_id: str) -> str:
        return self._get(f"/playlists/{playlist_id}", {"fields": "snapshot_id"})["snapshot_id"]

    def get_tracks(self, playlist_id: str) -> tuple[list[Track], int]:
        """Return the playlist's tracks and how many entries had to be skipped.

        Local files, podcast episodes and tracks without an ISRC can't be
        matched, so they are left out.
        """
        tracks: list[Track] = []
        skipped = 0
        offset = 0
        while True:
            page = self._get(
                f"/playlists/{playlist_id}/items",
                {"fields": ITEM_FIELDS, "limit": PAGE_SIZE, "offset": offset},
            )
            for entry in page.get("items") or []:
                track = _parse_track(entry)
                if track:
                    tracks.append(track)
                else:
                    skipped += 1
            if not page.get("next"):
                return tracks, skipped
            offset += PAGE_SIZE

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = send(self._http, "GET", f"{API_URL}{path}", params=params, headers=self._headers)
        if response.status_code in (403, 404):
            raise SpotifyError(
                "Spotify playlist not found or not accessible. Only playlists you own "
                "or collaborate on can be synced."
            )
        if not response.is_success:
            raise SpotifyError(f"Spotify API error {response.status_code}: {response.text[:300]}")
        return response.json()


def normalize_isrc(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _parse_track(entry: dict[str, Any]) -> Track | None:
    item = entry.get("item")
    if entry.get("is_local") or not isinstance(item, dict) or item.get("type") != "track":
        return None
    isrc = normalize_isrc((item.get("external_ids") or {}).get("isrc"))
    if not isrc or not item.get("id"):
        return None
    return Track(
        id=item["id"],
        isrc=isrc,
        name=item.get("name") or "",
        artists=tuple(a["name"] for a in item.get("artists") or [] if a.get("name")),
        duration_ms=item.get("duration_ms") or 0,
    )


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error")
    except (ValueError, AttributeError):
        return None

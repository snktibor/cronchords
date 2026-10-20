"""Reads and writes the sync state through Supabase's REST (PostgREST) API."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .http import send

# Keeps the "isrc=in.(...)" filter well below URL length limits.
CACHE_LOOKUP_CHUNK = 100


class DatabaseError(Exception):
    pass


@dataclass(frozen=True)
class AppConfig:
    spotify_client_id: str | None = None
    spotify_refresh_token: str | None = None
    spotify_connected_at: datetime | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    youtube_refresh_token: str | None = None

    def missing(self) -> list[str]:
        """Settings that still have to be filled in from the dashboard."""
        required = {
            "Spotify client ID": self.spotify_client_id,
            "Spotify connection": self.spotify_refresh_token,
            "Google client ID": self.google_client_id,
            "Google client secret": self.google_client_secret,
            "YouTube connection": self.youtube_refresh_token,
        }
        return [name for name, value in required.items() if not value]


@dataclass(frozen=True)
class Mapping:
    id: str
    spotify_playlist_id: str
    youtube_playlist_id: str
    spotify_snapshot_id: str | None = None
    spotify_playlist_name: str | None = None

    @property
    def label(self) -> str:
        return self.spotify_playlist_name or self.spotify_playlist_id


@dataclass(frozen=True)
class CacheEntry:
    isrc: str
    spotify_track_id: str
    youtube_video_id: str | None
    searched_at: datetime


class Database:
    def __init__(self, url: str, key: str, *, transport: httpx.BaseTransport | None = None):
        headers = {"apikey": key}
        # Legacy service_role keys are JWTs and go in the Authorization header too.
        # The newer sb_secret_... keys are not JWTs and must only be sent as apikey.
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers=headers,
            timeout=30,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def get_config(self) -> AppConfig:
        rows = self._get("/app_config", {"id": "eq.1", "select": "*"})
        if not rows:
            raise DatabaseError(
                "app_config is empty. Run the SQL migration, and make sure "
                "SUPABASE_SECRET_KEY is the secret key, not the publishable one."
            )
        row = rows[0]
        return AppConfig(
            spotify_client_id=row.get("spotify_client_id"),
            spotify_refresh_token=row.get("spotify_refresh_token"),
            spotify_connected_at=_parse_time(row.get("spotify_connected_at")),
            google_client_id=row.get("google_client_id"),
            google_client_secret=row.get("google_client_secret"),
            youtube_refresh_token=row.get("youtube_refresh_token"),
        )

    def update_config(self, **fields: Any) -> None:
        self._patch("/app_config", {"id": "eq.1"}, fields)

    def get_mappings(self) -> list[Mapping]:
        rows = self._get(
            "/playlist_mappings",
            {
                "select": "id,spotify_playlist_id,youtube_playlist_id,"
                "spotify_snapshot_id,spotify_playlist_name",
                "order": "created_at",
            },
        )
        return [Mapping(**row) for row in rows]

    def update_mapping(self, mapping_id: str, **fields: Any) -> None:
        self._patch("/playlist_mappings", {"id": f"eq.{mapping_id}"}, fields)

    def get_cache_entries(self, isrcs: Iterable[str]) -> dict[str, CacheEntry]:
        wanted = sorted(set(isrcs))
        entries: dict[str, CacheEntry] = {}
        for start in range(0, len(wanted), CACHE_LOOKUP_CHUNK):
            chunk = wanted[start : start + CACHE_LOOKUP_CHUNK]
            rows = self._get(
                "/track_sync_cache", {"select": "*", "isrc": f"in.({','.join(chunk)})"}
            )
            for row in rows:
                entries[row["isrc"]] = CacheEntry(
                    isrc=row["isrc"],
                    spotify_track_id=row["spotify_track_id"],
                    youtube_video_id=row.get("youtube_video_id"),
                    searched_at=_parse_time(row["searched_at"]),
                )
        return entries

    def save_cache_entry(self, entry: CacheEntry) -> None:
        response = send(
            self._http,
            "POST",
            "/track_sync_cache",
            json={
                "isrc": entry.isrc,
                "spotify_track_id": entry.spotify_track_id,
                "youtube_video_id": entry.youtube_video_id,
                "searched_at": entry.searched_at.isoformat(),
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        _check(response)

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = send(self._http, "GET", path, params=params)
        _check(response)
        return response.json()

    def _patch(self, path: str, params: dict[str, str], fields: dict[str, Any]) -> None:
        response = send(
            self._http,
            "PATCH",
            path,
            params=params,
            json=fields,
            headers={"Prefer": "return=minimal"},
        )
        _check(response)


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        message = response.json().get("message") or response.text
    except (ValueError, AttributeError):
        message = response.text
    raise DatabaseError(f"Supabase request failed ({response.status_code}): {message[:300]}")


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None

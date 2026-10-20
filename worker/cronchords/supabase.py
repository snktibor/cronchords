"""Reads and writes the sync state through Supabase's REST (PostgREST) API."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx

from .http import send

# Keeps "column=in.(...)" filters well below URL length limits.
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


class Direction(StrEnum):
    SPOTIFY_TO_YOUTUBE = "spotify_to_youtube"
    YOUTUBE_TO_SPOTIFY = "youtube_to_spotify"
    BOTH = "both"


@dataclass(frozen=True)
class Mapping:
    id: str
    spotify_playlist_id: str
    youtube_playlist_id: str
    direction: Direction = Direction.SPOTIFY_TO_YOUTUBE
    spotify_snapshot_id: str | None = None
    youtube_snapshot_id: str | None = None
    # (Spotify track id, YouTube video id) pairs, see sync.py
    synced_songs: frozenset[tuple[str, str]] = frozenset()
    spotify_playlist_name: str | None = None

    @property
    def label(self) -> str:
        return self.spotify_playlist_name or self.spotify_playlist_id

    @property
    def to_youtube(self) -> bool:
        return self.direction is not Direction.YOUTUBE_TO_SPOTIFY

    @property
    def to_spotify(self) -> bool:
        return self.direction is not Direction.SPOTIFY_TO_YOUTUBE


@dataclass(frozen=True)
class CacheEntry:
    """A Spotify track's song on YouTube."""

    isrc: str
    spotify_track_id: str
    youtube_video_id: str | None
    searched_at: datetime


@dataclass(frozen=True)
class VideoCacheEntry:
    """A YouTube video's song on Spotify."""

    youtube_video_id: str
    spotify_track_id: str | None
    isrc: str | None
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
                "select": "id,spotify_playlist_id,youtube_playlist_id,direction,"
                "spotify_snapshot_id,youtube_snapshot_id,synced_songs,spotify_playlist_name",
                "order": "created_at",
            },
        )
        return [_mapping(row) for row in rows]

    def update_mapping(self, mapping_id: str, **fields: Any) -> None:
        self._patch("/playlist_mappings", {"id": f"eq.{mapping_id}"}, fields)

    def get_cache_entries(self, isrcs: Iterable[str]) -> dict[str, CacheEntry]:
        rows = self._lookup("/track_sync_cache", "isrc", isrcs)
        return {row["isrc"]: _cache_entry(row) for row in rows}

    def get_cache_entries_for_videos(self, video_ids: Iterable[str]) -> dict[str, list[CacheEntry]]:
        """The Spotify tracks that were matched to these videos, by video id."""
        entries: dict[str, list[CacheEntry]] = {}
        for row in self._lookup("/track_sync_cache", "youtube_video_id", video_ids):
            entries.setdefault(row["youtube_video_id"], []).append(_cache_entry(row))
        return entries

    def save_cache_entry(self, entry: CacheEntry, *, overwrite: bool = True) -> None:
        self._upsert(
            "/track_sync_cache",
            {
                "isrc": entry.isrc,
                "spotify_track_id": entry.spotify_track_id,
                "youtube_video_id": entry.youtube_video_id,
                "searched_at": entry.searched_at.isoformat(),
            },
            overwrite=overwrite,
        )

    def get_video_cache_entries(self, video_ids: Iterable[str]) -> dict[str, VideoCacheEntry]:
        rows = self._lookup("/video_sync_cache", "youtube_video_id", video_ids)
        return {
            row["youtube_video_id"]: VideoCacheEntry(
                youtube_video_id=row["youtube_video_id"],
                spotify_track_id=row.get("spotify_track_id"),
                isrc=row.get("isrc"),
                searched_at=_parse_time(row["searched_at"]),
            )
            for row in rows
        }

    def save_video_cache_entry(self, entry: VideoCacheEntry) -> None:
        self._upsert(
            "/video_sync_cache",
            {
                "youtube_video_id": entry.youtube_video_id,
                "spotify_track_id": entry.spotify_track_id,
                "isrc": entry.isrc,
                "searched_at": entry.searched_at.isoformat(),
            },
        )

    def _lookup(self, path: str, column: str, values: Iterable[str]) -> list[dict[str, Any]]:
        wanted = sorted(set(values))
        rows = []
        for start in range(0, len(wanted), CACHE_LOOKUP_CHUNK):
            chunk = wanted[start : start + CACHE_LOOKUP_CHUNK]
            rows += self._get(path, {"select": "*", column: f"in.({','.join(chunk)})"})
        return rows

    def _upsert(self, path: str, row: dict[str, Any], *, overwrite: bool = True) -> None:
        resolution = "merge-duplicates" if overwrite else "ignore-duplicates"
        response = send(
            self._http,
            "POST",
            path,
            json=row,
            headers={"Prefer": f"resolution={resolution},return=minimal"},
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


def _mapping(row: dict[str, Any]) -> Mapping:
    return Mapping(
        id=row["id"],
        spotify_playlist_id=row["spotify_playlist_id"],
        youtube_playlist_id=row["youtube_playlist_id"],
        direction=Direction(row["direction"]),
        spotify_snapshot_id=row["spotify_snapshot_id"],
        youtube_snapshot_id=row["youtube_snapshot_id"],
        synced_songs=frozenset(tuple(pair) for pair in row["synced_songs"] or []),
        spotify_playlist_name=row["spotify_playlist_name"],
    )


def _cache_entry(row: dict[str, Any]) -> CacheEntry:
    return CacheEntry(
        isrc=row["isrc"],
        spotify_track_id=row["spotify_track_id"],
        youtube_video_id=row.get("youtube_video_id"),
        searched_at=_parse_time(row["searched_at"]),
    )


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None

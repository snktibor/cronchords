"""The parts of the YouTube Data API v3 the sync needs.

Quota, as of June 2026: search.list has its own bucket of 100 calls a day,
every other method shares 10,000 units a day. Reads cost 1 unit, inserts and
deletes 50. Both reset at midnight Pacific time.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .http import RETRY_STATUSES, send

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/youtube/v3"

QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})

# Changing a playlist occasionally fails with 409 ("operation aborted") when
# YouTube is busy. It is safe to retry.
WRITE_RETRY_STATUSES = RETRY_STATUSES | {409}


class YouTubeError(Exception):
    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason


class YouTubeAuthError(YouTubeError):
    """Google rejected the stored credentials, YouTube has to be reconnected."""


class QuotaExceededError(YouTubeError):
    """The shared daily quota is used up."""


class SearchQuotaExceededError(YouTubeError):
    """The daily search.list allowance is used up. Other calls may still work."""


class VideoUnavailableError(YouTubeError):
    """The video can't be added to a playlist, usually because it was removed."""


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    # The uploader. Empty for deleted and private videos in a playlist.
    channel: str
    duration_s: int | None = None

    def __str__(self) -> str:
        return f"{self.title} (https://youtu.be/{self.id})"


@dataclass(frozen=True)
class PlaylistItem:
    id: str
    video: Video
    added_at: datetime | None = None


def refresh_access_token(
    http: httpx.Client, client_id: str, client_secret: str, refresh_token: str
) -> str:
    response = send(
        http,
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if response.is_success:
        return response.json()["access_token"]
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if error in ("invalid_grant", "invalid_client", "unauthorized_client"):
        raise YouTubeAuthError(
            f"Google rejected the stored credentials ({error}). Reconnect YouTube in the "
            "dashboard. If this keeps happening every 7 days, publish your OAuth app "
            "(see the README).",
            reason=error,
        )
    raise YouTubeError(f"Google token request failed ({response.status_code}): {error}")


class YouTubeClient:
    def __init__(self, http: httpx.Client, access_token: str):
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self.units_used = 0
        self.searches = 0

    def playlist_items(self, playlist_id: str) -> list[PlaylistItem]:
        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
            "fields": (
                "nextPageToken,"
                "items(id,snippet(publishedAt,title,videoOwnerChannelTitle,resourceId/videoId))"
            ),
        }
        items: list[PlaylistItem] = []
        while True:
            page = self._call("GET", "playlistItems", params=params)
            for item in page.get("items", []):
                snippet = item["snippet"]
                video = Video(
                    id=snippet["resourceId"]["videoId"],
                    title=snippet.get("title") or "",
                    channel=snippet.get("videoOwnerChannelTitle") or "",
                )
                # When the video was added to the playlist, not when it was uploaded.
                published = snippet.get("publishedAt")
                added_at = datetime.fromisoformat(published) if published else None
                items.append(PlaylistItem(item["id"], video, added_at))
            if not page.get("nextPageToken"):
                return items
            params["pageToken"] = page["nextPageToken"]

    def search(self, query: str, limit: int = 10) -> list[Video]:
        page = self._call(
            "GET",
            "search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoCategoryId": "10",  # Music
                "maxResults": limit,
                "fields": "items(id/videoId,snippet(title,channelTitle))",
            },
        )
        return [
            Video(
                id=item["id"]["videoId"],
                # Search snippets come HTML-escaped ("Guns N&#39; Roses").
                title=html.unescape(item["snippet"]["title"]),
                channel=html.unescape(item["snippet"]["channelTitle"]),
            )
            for item in page.get("items", [])
        ]

    def durations(self, video_ids: list[str]) -> dict[str, int]:
        """Video lengths in seconds, for up to 50 videos at a time."""
        if not video_ids:
            return {}
        page = self._call(
            "GET",
            "videos",
            params={
                "part": "contentDetails",
                "id": ",".join(video_ids),
                "fields": "items(id,contentDetails/duration)",
            },
        )
        durations = {}
        for item in page.get("items", []):
            seconds = parse_duration(item["contentDetails"].get("duration", ""))
            if seconds is not None:
                durations[item["id"]] = seconds
        return durations

    def add_to_playlist(self, playlist_id: str, video_id: str) -> PlaylistItem:
        item = self._call(
            "POST",
            "playlistItems",
            params={"part": "snippet"},
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )
        return PlaylistItem(item["id"], Video(video_id, title="", channel=""))

    def remove_from_playlist(self, item_id: str) -> None:
        self._call("DELETE", "playlistItems", params={"id": item_id})

    def _call(self, method: str, resource: str, **kwargs: Any) -> dict[str, Any]:
        is_search = resource == "search"
        is_write = method != "GET"
        if is_search:
            self.searches += 1
        else:
            self.units_used += 50 if is_write else 1

        response = send(
            self._http,
            method,
            f"{API_URL}/{resource}",
            headers=self._headers,
            retry_statuses=WRITE_RETRY_STATUSES if is_write else RETRY_STATUSES,
            **kwargs,
        )
        if response.is_success:
            # Deleting answers with 204 No Content.
            return response.json() if response.content else {}

        message, reason = _error_details(response)
        if reason in QUOTA_REASONS:
            if is_search:
                raise SearchQuotaExceededError(message, reason)
            raise QuotaExceededError(message, reason)
        if response.status_code == 401:
            raise YouTubeAuthError(message, reason)
        if reason == "videoNotFound":
            raise VideoUnavailableError(message, reason)
        raise YouTubeError(f"YouTube API error {response.status_code}: {message}", reason)


def parse_duration(value: str) -> int | None:
    """Parse an ISO 8601 duration such as PT4M13S into seconds."""
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match or value in ("P", "PT"):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _error_details(response: httpx.Response) -> tuple[str, str | None]:
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return response.text[:300], None
    reasons = [e.get("reason") for e in error.get("errors") or [] if e.get("reason")]
    # Google puts HTML links into some messages.
    message = re.sub(r"<[^>]+>", "", error.get("message") or "")
    return message, reasons[0] if reasons else None

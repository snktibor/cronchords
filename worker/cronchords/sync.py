from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from . import matching
from .spotify import SpotifyClient, SpotifyError, Track
from .supabase import CacheEntry, Database, Mapping
from .youtube import (
    QuotaExceededError,
    SearchQuotaExceededError,
    VideoUnavailableError,
    YouTubeClient,
    YouTubeError,
)

log = logging.getLogger(__name__)

# Songs without a usable YouTube match are searched again after this long.
RETRY_UNMATCHED_AFTER = timedelta(days=30)


@dataclass
class RunResult:
    synced: int = 0
    failed: int = 0
    out_of_quota: bool = False


class Syncer:
    def __init__(
        self,
        db: Database,
        spotify: SpotifyClient,
        youtube: YouTubeClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.db = db
        self.spotify = spotify
        self.youtube = youtube
        self.now = now
        self.search_exhausted = False

    def run(self, mappings: list[Mapping]) -> RunResult:
        result = RunResult()
        for mapping in mappings:
            try:
                self.sync(mapping)
            except QuotaExceededError:
                log.warning(
                    "YouTube API quota is used up for today. Syncing continues after it "
                    "resets at midnight Pacific time."
                )
                self.db.update_mapping(
                    mapping.id, last_error="Waiting for the YouTube API quota to reset"
                )
                result.out_of_quota = True
                break
            except (SpotifyError, YouTubeError) as exc:
                log.error("[%s] %s", mapping.label, exc)
                self.db.update_mapping(mapping.id, last_error=str(exc))
                result.failed += 1
            else:
                result.synced += 1
        return result

    def sync(self, mapping: Mapping) -> None:
        snapshot_id = self.spotify.get_snapshot_id(mapping.spotify_playlist_id)
        if snapshot_id == mapping.spotify_snapshot_id:
            log.info("[%s] unchanged since the last sync", mapping.label)
            self.db.update_mapping(
                mapping.id, last_synced_at=self.now().isoformat(), last_error=None
            )
            return

        tracks, skipped = self.spotify.get_tracks(mapping.spotify_playlist_id)
        tracks = _unique(tracks)
        cache = self.db.get_cache_entries(track.isrc for track in tracks)
        on_youtube = self.youtube.playlist_video_ids(mapping.youtube_playlist_id)
        log.info(
            "[%s] %d tracks (%d skipped: local files, episodes or no ISRC), %d not searched yet",
            mapping.label,
            len(tracks),
            skipped,
            sum(1 for track in tracks if track.isrc not in cache),
        )

        added = unmatched = pending = 0
        for track in tracks:
            entry = cache.get(track.isrc)
            if entry is None or self._should_search_again(entry):
                if self.search_exhausted:
                    pending += 1
                    continue
                try:
                    entry = self._search(track)
                except SearchQuotaExceededError:
                    log.warning(
                        "Daily YouTube search limit reached, the remaining songs are "
                        "searched after it resets"
                    )
                    self.search_exhausted = True
                    pending += 1
                    continue

            video_id = entry.youtube_video_id
            if video_id is None:
                unmatched += 1
                continue
            if video_id in on_youtube:
                continue
            try:
                self.youtube.add_to_playlist(mapping.youtube_playlist_id, video_id)
            except VideoUnavailableError:
                log.info("[%s] %s: video %s is gone, skipping", mapping.label, track, video_id)
                self.db.save_cache_entry(
                    replace(entry, youtube_video_id=None, searched_at=self.now())
                )
                unmatched += 1
                continue
            on_youtube.add(video_id)
            added += 1
            log.info("[%s] added %s (https://youtu.be/%s)", mapping.label, track, video_id)

        log.info(
            "[%s] %d added, %d without a match, %d left for later",
            mapping.label,
            added,
            unmatched,
            pending,
        )
        if pending:
            # Keep the old snapshot id so the next run picks up where this one stopped.
            self.db.update_mapping(
                mapping.id,
                last_error=f"{pending} song(s) still to sync, waiting for the YouTube search limit",
            )
        else:
            self.db.update_mapping(
                mapping.id,
                spotify_snapshot_id=snapshot_id,
                last_synced_at=self.now().isoformat(),
                last_error=None,
            )

    def _search(self, track: Track) -> CacheEntry:
        videos = self.youtube.search(matching.search_query(track))
        durations = self.youtube.durations([video.id for video in videos])
        videos = [replace(video, duration_s=durations.get(video.id)) for video in videos]
        best = matching.best_match(track, videos)
        if best is None:
            log.info("no good match on YouTube for %s", track)
        entry = CacheEntry(
            isrc=track.isrc,
            spotify_track_id=track.id,
            youtube_video_id=best.id if best else None,
            searched_at=self.now(),
        )
        self.db.save_cache_entry(entry)
        return entry

    def _should_search_again(self, entry: CacheEntry) -> bool:
        return (
            entry.youtube_video_id is None
            and self.now() - entry.searched_at >= RETRY_UNMATCHED_AFTER
        )


def _unique(tracks: list[Track]) -> list[Track]:
    seen: set[str] = set()
    unique = []
    for track in tracks:
        if track.isrc not in seen:
            seen.add(track.isrc)
            unique.append(track)
    return unique

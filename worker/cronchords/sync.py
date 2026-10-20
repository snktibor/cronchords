"""Keeps the two playlists of each mapping in sync.

A mapping syncs from Spotify to YouTube, from YouTube to Spotify, or both
ways. One-way syncs only add songs. Two-way syncs remove songs too, which
takes a memory of the last sync: the mapping's synced_songs, the (Spotify
track, YouTube video) pairs that were on both sides after it. If one half of
a pair has disappeared since, the song was removed on that side, so the other
half is removed as well instead of being copied back.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from . import matching
from .spotify import (
    WRITE_BATCH,
    PlaylistChangedError,
    SpotifyClient,
    SpotifyError,
    SpotifyRateLimitError,
    Track,
)
from .supabase import CacheEntry, Database, Direction, Mapping, VideoCacheEntry
from .youtube import (
    PlaylistItem,
    QuotaExceededError,
    SearchQuotaExceededError,
    Video,
    VideoUnavailableError,
    YouTubeClient,
    YouTubeError,
)

log = logging.getLogger(__name__)

# Songs without a usable match are searched again after this long.
RETRY_UNMATCHED_AFTER = timedelta(days=30)

# videos.list takes up to 50 ids at a time.
VIDEO_BATCH = 50

DIRECTIONS = {
    Direction.SPOTIFY_TO_YOUTUBE: "Spotify → YouTube",
    Direction.YOUTUBE_TO_SPOTIFY: "YouTube → Spotify",
    Direction.BOTH: "both ways",
}

# A Spotify track id and a YouTube video id of the same song.
Link = tuple[str, str]

T = TypeVar("T")


@dataclass
class RunResult:
    synced: int = 0
    failed: int = 0
    # A quota or rate limit ran out, the remaining mappings wait for the next run.
    stopped_early: bool = False


@dataclass
class Changes:
    """What syncing one mapping did."""

    added_to_youtube: int = 0
    added_to_spotify: int = 0
    removed_from_youtube: int = 0
    removed_from_spotify: int = 0
    unmatched: int = 0
    pending: int = 0

    @property
    def changed_youtube(self) -> bool:
        return bool(self.added_to_youtube or self.removed_from_youtube)

    def __str__(self) -> str:
        counts = {
            "added to YouTube": self.added_to_youtube,
            "added to Spotify": self.added_to_spotify,
            "removed from YouTube": self.removed_from_youtube,
            "removed from Spotify": self.removed_from_spotify,
            "without a match": self.unmatched,
            "left for later": self.pending,
        }
        return ", ".join(f"{n} {what}" for what, n in counts.items() if n) or "nothing to change"


class Playlists:
    """What the two playlists of a mapping hold right now."""

    def __init__(self, tracks: Iterable[Track], items: Iterable[PlaylistItem]):
        self.tracks: dict[str, Track] = {}
        self._by_isrc: dict[str, str] = {}
        for track in tracks:
            self.tracks.setdefault(track.id, track)
            if track.isrc:
                self._by_isrc.setdefault(track.isrc, track.id)
        self.items: dict[str, list[PlaylistItem]] = {}
        # Deleted and private videos stay listed, but their song isn't there anymore.
        self.dead: set[str] = set()
        for item in items:
            if item.video.channel:
                self.items.setdefault(item.video.id, []).append(item)
            else:
                self.dead.add(item.video.id)

    def video(self, video_id: str) -> Video:
        return self.items[video_id][0].video

    def find_track(self, track_id: str, isrc: str | None) -> str | None:
        """The track on the Spotify playlist that is this track or the same recording."""
        if track_id in self.tracks:
            return track_id
        return self._by_isrc.get(isrc) if isrc else None


@dataclass
class SyncedSongs:
    """The pairs a two-way mapping remembers, see the top of this module.

    They are kept up to date while syncing, so that a run that fails halfway
    still records what it changed. The next run would undo it otherwise.
    """

    linked: set[Link]
    # Remembered from the last run, but a half of them is gone since.
    unresolved: set[Link]
    # Unresolved pairs to look at again in the next run.
    kept: set[Link] = field(default_factory=set)

    @classmethod
    def load(cls, previous: Iterable[Link], playlists: Playlists) -> SyncedSongs:
        # A pair whose video died is forgotten: the track gets a new video, and
        # removing the dead entry later on leaves the track alone.
        alive = {(t, v) for t, v in previous if v not in playlists.dead}
        linked = {(t, v) for t, v in alive if t in playlists.tracks and v in playlists.items}
        return cls(linked, alive - linked)

    def forget_tracks(self, track_ids: Iterable[str]) -> None:
        gone = set(track_ids)
        self.unresolved = {(t, v) for t, v in self.unresolved if t not in gone}

    def forget_videos(self, video_ids: Iterable[str]) -> None:
        gone = set(video_ids)
        self.unresolved = {(t, v) for t, v in self.unresolved if v not in gone}


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
                result.stopped_early = True
                break
            except SpotifyRateLimitError as exc:
                log.warning("%s", exc)
                self.db.update_mapping(
                    mapping.id, last_error="Waiting for Spotify's rate limit to pass"
                )
                result.stopped_early = True
                break
            except (SpotifyError, YouTubeError) as exc:
                log.error("[%s] %s", mapping.label, exc)
                self.db.update_mapping(mapping.id, last_error=str(exc))
                result.failed += 1
            else:
                result.synced += 1
        return result

    def sync(self, mapping: Mapping) -> None:
        spotify_snapshot = self.spotify.get_snapshot_id(mapping.spotify_playlist_id)
        items = youtube_snapshot = None
        if mapping.to_spotify:
            # YouTube playlists have no snapshot ids, so they are listed on every run.
            items = self.youtube.playlist_items(mapping.youtube_playlist_id)
            youtube_snapshot = fingerprint(items)
        unchanged = (
            spotify_snapshot == mapping.spotify_snapshot_id
            and youtube_snapshot == mapping.youtube_snapshot_id
        )
        if unchanged:
            log.info("[%s] unchanged since the last sync", mapping.label)
            self.db.update_mapping(
                mapping.id, last_synced_at=self.now().isoformat(), last_error=None
            )
            return

        try:
            tracks, skipped = self.spotify.get_tracks(mapping.spotify_playlist_id, spotify_snapshot)
        except PlaylistChangedError as exc:
            log.info("[%s] %s, trying again in the next run", mapping.label, exc)
            return
        if items is None:
            items = self.youtube.playlist_items(mapping.youtube_playlist_id)
        playlists = Playlists(tracks, items)
        log.info(
            "[%s] syncing %s: %d tracks on Spotify (%d local files or episodes skipped), "
            "%d videos on YouTube (%d deleted or private ones skipped)",
            mapping.label,
            DIRECTIONS[mapping.direction],
            len(playlists.tracks),
            skipped,
            len(playlists.items),
            len(playlists.dead),
        )

        changes = Changes()
        two_way = mapping.direction is Direction.BOTH
        songs = SyncedSongs.load(mapping.synced_songs if two_way else (), playlists)
        try:
            problem = self._sync(mapping, playlists, songs, changes)
        except Exception:
            if two_way:
                self.db.update_mapping(
                    mapping.id, synced_songs=sorted(songs.linked | songs.unresolved)
                )
            raise
        log.info("[%s] %s", mapping.label, changes)
        if problem:
            log.warning("[%s] %s", mapping.label, problem)

        fields: dict[str, Any] = {}
        if two_way:
            fields["synced_songs"] = sorted(songs.linked | songs.kept)
        if changes.pending:
            # Keep the old snapshot ids, so the next run picks up where this one stopped.
            fields["last_error"] = (
                f"{changes.pending} song(s) still to sync, waiting for the YouTube search limit"
            )
        elif problem:
            fields["last_error"] = problem
        else:
            fields |= {
                "spotify_snapshot_id": spotify_snapshot,
                # A fingerprint taken before this run's own changes would match
                # again if someone undid them, and that would go unnoticed.
                "youtube_snapshot_id": None if changes.changed_youtube else youtube_snapshot,
                "last_synced_at": self.now().isoformat(),
                "last_error": None,
            }
        self.db.update_mapping(mapping.id, **fields)

    def _sync(
        self, mapping: Mapping, playlists: Playlists, songs: SyncedSongs, changes: Changes
    ) -> str | None:
        """Update both playlists, returning a problem to show in the dashboard if any."""
        youtube_was_empty = not playlists.items
        spotify_was_empty = not playlists.tracks
        known = songs.linked | songs.unresolved
        # Songs whose counterpart has disappeared since the last sync. They get
        # removed too, unless they turn out to match something else.
        orphan_tracks = {t for t, _ in songs.unresolved if t in playlists.tracks}
        orphan_tracks -= _tracks(songs.linked)
        orphan_videos = {v for _, v in songs.unresolved if v in playlists.items}
        orphan_videos -= _videos(songs.linked)
        new_tracks = [t for t in playlists.tracks if t not in _tracks(known)]
        new_videos = [v for v in playlists.items if v not in _videos(known)]

        tracks_to_add: dict[str, str] = {}
        if mapping.to_spotify:
            # Videos go first: looking them up on Spotify is free, YouTube searches are not.
            found, tracks_to_add = self._find_tracks(playlists, new_videos, orphan_videos, changes)
            songs.linked |= {(track_id, video_id) for video_id, track_id in found.items()}
        if mapping.to_youtube:
            linked = _tracks(songs.linked)
            self._find_videos(
                mapping,
                playlists,
                songs,
                new=[t for t in new_tracks if t not in linked],
                orphans=[t for t in orphan_tracks if t not in linked],
                changes=changes,
            )
        linked = _videos(songs.linked)
        tracks_to_add = {v: t for v, t in tracks_to_add.items() if v not in linked}
        if tracks_to_add:
            self._add_to_spotify(mapping, playlists, songs, tracks_to_add, changes)

        problem = None
        removed_on_youtube = orphan_tracks - _tracks(songs.linked)
        removed_on_spotify = orphan_videos - _videos(songs.linked)
        if removed_on_youtube:
            if youtube_was_empty:
                # More likely a hiccup than a deliberate purge.
                problem = (
                    "The YouTube playlist came back empty, so nothing was removed from Spotify"
                )
                songs.kept |= {(t, v) for t, v in songs.unresolved if t in removed_on_youtube}
            else:
                self._remove_from_spotify(mapping, playlists, songs, removed_on_youtube, changes)
        if removed_on_spotify:
            if spotify_was_empty:
                problem = (
                    "The Spotify playlist came back empty, so nothing was removed from YouTube"
                )
                songs.kept |= {(t, v) for t, v in songs.unresolved if v in removed_on_spotify}
            else:
                self._remove_from_youtube(mapping, playlists, songs, removed_on_spotify, changes)
        return problem

    def _find_tracks(
        self,
        playlists: Playlists,
        new_videos: list[str],
        orphan_videos: Iterable[str],
        changes: Changes,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Work out which Spotify track each video is.

        Returns two video id -> track id maps: videos whose song is on the
        Spotify playlist already, and videos whose track has to be added there.
        Orphaned videos are only compared with what's on Spotify, never searched.
        """
        video_ids = [*new_videos, *orphan_videos]
        if not video_ids:
            return {}, {}
        matched_to_tracks = self.db.get_cache_entries_for_videos(video_ids)
        searched = self.db.get_video_cache_entries(video_ids)

        def known(video_id: str) -> list[tuple[str, str | None]]:
            """What the caches say the video is, as (track id, ISRC) pairs, best first."""
            pairs = [(e.spotify_track_id, e.isrc) for e in matched_to_tracks.get(video_id, [])]
            entry = searched.get(video_id)
            if entry and entry.spotify_track_id:
                # The video's own entry wins, it may have been corrected by hand.
                pairs.insert(0, (entry.spotify_track_id, entry.isrc))
            return pairs

        found: dict[str, str] = {}
        unknown: list[Video] = []
        for video_id in video_ids:
            on_spotify = [t for t in (playlists.find_track(*k) for k in known(video_id)) if t]
            if on_spotify:
                found[video_id] = on_spotify[0]
            else:
                unknown.append(playlists.video(video_id))

        # The song may be on Spotify as another release, or the video may be a
        # music video of it, so compare the rest with the playlist first.
        finder = matching.TrackFinder(playlists.tracks.values())
        is_new = set(new_videos)
        to_add: dict[str, str] = {}
        for video in self._with_durations(unknown):
            track = finder.find(video)
            if track:
                found[video.id] = track.id
                continue
            if video.id not in is_new:
                continue
            if cached := known(video.id):
                to_add[video.id] = cached[0][0]
                continue
            entry = searched.get(video.id)
            if entry is None or self._should_search_again(entry.searched_at):
                entry = self._search_spotify(video)
            if entry.spotify_track_id is None:
                changes.unmatched += 1
                continue
            on_spotify = playlists.find_track(entry.spotify_track_id, entry.isrc)
            if on_spotify:
                found[video.id] = on_spotify
            else:
                to_add[video.id] = entry.spotify_track_id
        return found, to_add

    def _find_videos(
        self,
        mapping: Mapping,
        playlists: Playlists,
        songs: SyncedSongs,
        *,
        new: list[str],
        orphans: list[str],
        changes: Changes,
    ) -> None:
        """Find each track's video and add the new tracks' videos to YouTube.

        Orphaned tracks only stay if the cache knows another video of theirs
        that is on the YouTube playlist.
        """
        tracks = [playlists.tracks[track_id] for track_id in [*new, *orphans]]
        cache = self.db.get_cache_entries(track.isrc for track in tracks if track.isrc)
        is_new = set(new)
        for track in tracks:
            entry = cache.get(track.isrc) if track.isrc else None
            if track.isrc and track.id in is_new and self._needs_search(entry, playlists):
                if self.search_exhausted:
                    changes.pending += 1
                    continue
                try:
                    entry = cache[track.isrc] = self._search_youtube(track)
                except SearchQuotaExceededError:
                    log.warning(
                        "Daily YouTube search limit reached, the remaining songs are "
                        "searched after it resets"
                    )
                    self.search_exhausted = True
                    changes.pending += 1
                    continue

            video_id = entry.youtube_video_id if entry else None
            if video_id is None or video_id in playlists.dead:
                # Not found on YouTube, or no ISRC to look the track up by.
                if track.id in is_new:
                    changes.unmatched += 1
                continue
            if video_id not in playlists.items:
                if track.id not in is_new:
                    continue
                try:
                    item = self.youtube.add_to_playlist(mapping.youtube_playlist_id, video_id)
                except VideoUnavailableError:
                    log.info("[%s] %s: video %s is gone, skipping", mapping.label, track, video_id)
                    entry = cache[track.isrc] = replace(
                        entry, youtube_video_id=None, searched_at=self.now()
                    )
                    self.db.save_cache_entry(entry)
                    changes.unmatched += 1
                    continue
                playlists.items[video_id] = [item]
                changes.added_to_youtube += 1
                log.info("[%s] added %s (https://youtu.be/%s)", mapping.label, track, video_id)
            songs.linked.add((track.id, video_id))

    def _add_to_spotify(
        self,
        mapping: Mapping,
        playlists: Playlists,
        songs: SyncedSongs,
        tracks_to_add: dict[str, str],
        changes: Changes,
    ) -> None:
        # Several videos can be the same song, add it once.
        first_video: dict[str, str] = {}
        for video_id, track_id in tracks_to_add.items():
            first_video.setdefault(track_id, video_id)
        for batch in _chunks(list(first_video), WRITE_BATCH):
            self.spotify.add_tracks(mapping.spotify_playlist_id, batch)
            added = set(batch)
            songs.linked |= {(t, v) for v, t in tracks_to_add.items() if t in added}
            changes.added_to_spotify += len(batch)
            for track_id in batch:
                log.info(
                    "[%s] added https://open.spotify.com/track/%s for %s",
                    mapping.label,
                    track_id,
                    playlists.video(first_video[track_id]),
                )

    def _remove_from_spotify(
        self,
        mapping: Mapping,
        playlists: Playlists,
        songs: SyncedSongs,
        track_ids: set[str],
        changes: Changes,
    ) -> None:
        # Paging through a playlist that changes meanwhile can skip videos. Make
        # sure they are really gone before removing their songs from Spotify.
        listed = {
            item.video.id for item in self.youtube.playlist_items(mapping.youtube_playlist_id)
        }
        reappeared = {t for t, v in songs.unresolved if t in track_ids and v in listed}
        if reappeared:
            songs.kept |= {(t, v) for t, v in songs.unresolved if t in reappeared}
        for batch in _chunks(sorted(track_ids - reappeared), WRITE_BATCH):
            self.spotify.remove_tracks(mapping.spotify_playlist_id, batch)
            songs.forget_tracks(batch)
            changes.removed_from_spotify += len(batch)
            for track_id in batch:
                log.info(
                    "[%s] removed %s from Spotify, it's gone from YouTube",
                    mapping.label,
                    playlists.tracks[track_id],
                )

    def _remove_from_youtube(
        self,
        mapping: Mapping,
        playlists: Playlists,
        songs: SyncedSongs,
        video_ids: set[str],
        changes: Changes,
    ) -> None:
        for video_id in sorted(video_ids):
            for item in playlists.items[video_id]:
                self.youtube.remove_from_playlist(item.id)
            songs.forget_videos([video_id])
            changes.removed_from_youtube += 1
            log.info(
                "[%s] removed %s from YouTube, it's gone from Spotify",
                mapping.label,
                playlists.video(video_id),
            )

    def _search_youtube(self, track: Track) -> CacheEntry:
        videos = self.youtube.search(matching.youtube_query(track))
        best = matching.best_video(track, self._with_durations(videos))
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

    def _search_spotify(self, video: Video) -> VideoCacheEntry:
        best = matching.best_track(video, self.spotify.search(matching.spotify_query(video)))
        if best is None:
            log.info("no good match on Spotify for %s", video)
        entry = VideoCacheEntry(
            youtube_video_id=video.id,
            spotify_track_id=best.id if best else None,
            isrc=best.isrc if best else None,
            searched_at=self.now(),
        )
        self.db.save_video_cache_entry(entry)
        if best and best.isrc:
            # Playlists that sync this song to YouTube can then skip the search.
            self.db.save_cache_entry(
                CacheEntry(best.isrc, best.id, video.id, self.now()), overwrite=False
            )
        return entry

    def _with_durations(self, videos: list[Video]) -> list[Video]:
        durations: dict[str, int] = {}
        for batch in _chunks(videos, VIDEO_BATCH):
            durations |= self.youtube.durations([video.id for video in batch])
        return [replace(video, duration_s=durations.get(video.id)) for video in videos]

    def _needs_search(self, entry: CacheEntry | None, playlists: Playlists) -> bool:
        if entry is None or entry.youtube_video_id in playlists.dead:
            return True
        return entry.youtube_video_id is None and self._should_search_again(entry.searched_at)

    def _should_search_again(self, searched_at: datetime) -> bool:
        return self.now() - searched_at >= RETRY_UNMATCHED_AFTER


def fingerprint(items: Iterable[PlaylistItem]) -> str:
    """Stands in for the snapshot id that YouTube playlists don't have."""
    # A video that gets deleted or made private changes it too.
    videos = sorted({item.video.id + ("" if item.video.channel else "!") for item in items})
    return hashlib.blake2b(" ".join(videos).encode(), digest_size=16).hexdigest()


def _chunks(items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _tracks(links: Iterable[Link]) -> set[str]:
    return {track_id for track_id, _ in links}


def _videos(links: Iterable[Link]) -> set[str]:
    return {video_id for _, video_id in links}

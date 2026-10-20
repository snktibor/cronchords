"""Songs that can be found on both sides of the fake cloud."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fake_cloud import FakeCloud, spotify_entry

from cronchords.matching import spotify_query, youtube_query
from cronchords.spotify import Track
from cronchords.youtube import Video, parse_duration


@dataclass(frozen=True)
class Song:
    entry: dict[str, Any]  # as the Spotify playlist lists it
    video_id: str
    title: str
    channel: str
    duration: str

    @property
    def track_id(self) -> str:
        return self.entry["item"]["id"]

    @property
    def isrc(self) -> str:
        return self.entry["item"]["external_ids"]["isrc"]

    @property
    def track(self) -> Track:
        item = self.entry["item"]
        artists = tuple(artist["name"] for artist in item["artists"])
        return Track(item["id"], self.isrc, item["name"], artists, item["duration_ms"])

    @property
    def video(self) -> Video:
        return Video(self.video_id, self.title, self.channel, parse_duration(self.duration))


GET_LUCKY = Song(
    spotify_entry("USQX91300108", "Get Lucky", "Daft Punk", 248_000),
    "lucky",
    "Get Lucky",
    "Daft Punk - Topic",
    "PT4M8S",
)
RHAPSODY = Song(
    spotify_entry("GBUM71029604", "Bohemian Rhapsody - Remastered 2011", "Queen", 354_000),
    "rhapsody",
    "Bohemian Rhapsody (Remastered 2011)",
    "Queen - Topic",
    "PT5M54S",
)
DREAMS = Song(
    spotify_entry("USWB10002400", "Dreams", "Fleetwood Mac", 257_000),
    "dreams",
    "Dreams",
    "Fleetwood Mac - Topic",
    "PT4M17S",
)
HEY_JUDE = Song(
    spotify_entry("GBAYE0601690", "Hey Jude - Remastered 2015", "The Beatles", 431_000),
    "jude",
    "Hey Jude (Remastered 2015)",
    "The Beatles - Topic",
    "PT7M11S",
)


def stock(cloud: FakeCloud, *songs: Song) -> None:
    """Make the songs findable with a search on both services."""
    for song in songs:
        cloud.add_video(
            youtube_query(song.track), song.video_id, song.title, song.channel, song.duration
        )
        cloud.add_track(spotify_query(song.video), song.entry)

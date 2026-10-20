"""Picks the YouTube video that is most likely the same recording as a Spotify track."""

from __future__ import annotations

import re
import unicodedata

from .spotify import Track
from .youtube import Video

MIN_SCORE = 40

# Words that point to a different recording, unless the Spotify title has them too.
OTHER_VERSIONS = (
    "cover",
    "karaoke",
    "instrumental",
    "remix",
    "live",
    "acoustic",
    "nightcore",
    "sped up",
    "slowed",
    "reverb",
    "8d",
    "reaction",
    "tutorial",
    "lesson",
    "mashup",
    "bass boosted",
    "1 hour",
    "10 hours",
)


def search_query(track: Track) -> str:
    artist = track.artists[0] if track.artists else ""
    return f"{artist} - {track.name}" if artist else track.name


def best_match(track: Track, videos: list[Video]) -> Video | None:
    """Return the best scoring video, or None if nothing looks right."""
    if not videos:
        return None
    points, video = max(
        ((score(track, video, rank), video) for rank, video in enumerate(videos)),
        key=lambda pair: pair[0],
    )
    return video if points >= MIN_SCORE else None


def score(track: Track, video: Video, rank: int = 0) -> float:
    title = normalize(video.title)
    channel = normalize(video.channel)
    points = 0.0

    # The song title has to be in the video title.
    wanted = set(core_title(track.name).split())
    if wanted:
        coverage = len(wanted & set(title.split())) / len(wanted)
        points += 30 * coverage
        if coverage < 0.5:
            points -= 50

    artists = [a for a in (normalize(name) for name in track.artists) if a]
    if any(channel == f"{artist} topic" for artist in artists):
        # "Artist - Topic" channels hold the auto-generated album tracks, which
        # show up as proper songs in YouTube Music.
        points += 35
    elif any(_mentions(title, channel, artist) for artist in artists):
        points += 15

    if video.duration_s is not None and track.duration_ms:
        difference = abs(video.duration_s - track.duration_ms / 1000)
        if difference <= 3:
            points += 30
        elif difference <= 10:
            points += 15
        elif difference > 30:
            points -= 30

    name = normalize(track.name)
    for words in OTHER_VERSIONS:
        if _contains(title, words) and not _contains(name, words):
            points -= 50

    # YouTube's own ranking breaks ties.
    return points - rank


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: "Beyoncé - Topic" -> "beyonce topic"."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    return " ".join(re.findall(r"\w+", text))


def core_title(name: str) -> str:
    """The title without version info: "Song (feat. X) - Remastered 2011" -> "song"."""
    core = re.split(r"\s+-\s+", name, maxsplit=1)[0]
    core = re.sub(r"[(\[].*?[)\]]", " ", core)
    return normalize(core) or normalize(name)


def _mentions(title: str, channel: str, artist: str) -> bool:
    if _contains(title, artist) or _contains(channel, artist):
        return True
    # Channels like "QueenVEVO". Too loose for very short names.
    return len(artist) >= 4 and artist.replace(" ", "") in channel.replace(" ", "")


def _contains(text: str, words: str) -> bool:
    return f" {words} " in f" {text} "

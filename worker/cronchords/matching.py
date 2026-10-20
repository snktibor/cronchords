"""Decides whether a Spotify track and a YouTube video are the same recording."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from typing import TypeVar

from .spotify import Track
from .youtube import Video

MIN_SCORE = 40

# Words that point to a different recording, unless both sides have them.
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

# Words in video titles that say nothing about which song it is.
FILLER_WORDS = frozenset(
    {
        "official",
        "oficial",
        "officiel",
        "video",
        "videoclip",
        "clip",
        "music",
        "audio",
        "lyric",
        "lyrics",
        "letra",
        "visualizer",
        "visualiser",
        "hd",
        "hq",
        "4k",
        "mv",
        "remaster",
        "remastered",
        "ft",
        "feat",
        "featuring",
        "prod",
        "by",
        "with",
        "the",
        "and",
        "vs",
    }
)

BRACKETED = re.compile(r"[(\[][^)\]]*[)\]]")
BRACKETED_NOISE = re.compile(
    r"[(\[][^)\]]*\b(?:official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|ft|feat|featuring"
    r"|with|from|soundtrack|ost)\b[^)\]]*[)\]]",
    re.IGNORECASE,
)
LOOSE_NOISE = re.compile(
    r"\b(?:official\s+(?:music\s+)?(?:video|audio)|lyric\s+video|lyrics|m/v|mv|hd|hq|4k)\b"
    r"|[\"“”]",
    re.IGNORECASE,
)
FEATURING = re.compile(r"\s(?:ft|feat|featuring)\b.*?(?=\s[-–—|]\s|$)", re.IGNORECASE)
SEPARATOR = re.compile(r"\s[-–—|]\s")
CHANNEL_SUFFIX = re.compile(r"\s*(?:vevo|official)$", re.IGNORECASE)

T = TypeVar("T")


def youtube_query(track: Track) -> str:
    artist = track.artists[0] if track.artists else ""
    return f"{artist} - {track.name}" if artist else track.name


def spotify_query(video: Video) -> str:
    """Search terms for a video's song: its title without the "(Official Video)" parts."""
    terms = LOOSE_NOISE.sub(" ", FEATURING.sub("", BRACKETED_NOISE.sub("", video.title)))
    if video.channel.endswith(" - Topic"):
        # Auto-generated album tracks: the title is the song, the channel the artist.
        terms += " " + video.channel.removesuffix(" - Topic")
    elif not SEPARATOR.search(terms):
        # "Artist - Song" titles name the artist, otherwise only the channel does.
        terms += " " + CHANNEL_SUFFIX.sub("", video.channel)
    return " ".join(SEPARATOR.sub(" ", terms).split())


def best_video(track: Track, videos: list[Video]) -> Video | None:
    """Return the search result that best matches the track, or None if nothing looks right."""
    return _best((score(track, video, rank), video) for rank, video in enumerate(videos))


def best_track(video: Video, tracks: list[Track], *, ranked: bool = True) -> Track | None:
    """Return the track the video most likely is, or None if nothing looks right.

    With ranked=False the order of the tracks doesn't matter (they aren't search
    results, but for example the tracks of a playlist).
    """
    return _best(
        (score(track, video, rank if ranked else 0), track) for rank, track in enumerate(tracks)
    )


class TrackFinder:
    """Looks for a video's song among many tracks, such as a whole playlist.

    Unlike search results, these tracks weren't picked for the video, so a song
    that merely has the same title and length ("Intro") is likely among them.
    Only tracks whose artist the video names can match.
    """

    def __init__(self, tracks: Iterable[Track]):
        self._by_word: dict[str, list[Track]] = {}
        for track in tracks:
            for word in set(core_title(track.name).split()):
                self._by_word.setdefault(word, []).append(track)

    def find(self, video: Video) -> Track | None:
        candidates = {
            track.id: track
            for word in set(normalize(video.title).split())
            for track in self._by_word.get(word, [])
            if _artist_points(track, video) > 0
        }
        return best_track(video, list(candidates.values()), ranked=False)


def score(track: Track, video: Video, rank: int = 0) -> float:
    """How likely the video is a recording of the track. 40 or more counts as a match."""
    title = normalize(video.title)
    name = normalize(track.name)
    artists = [a for a in (normalize(artist) for artist in track.artists) if a]
    points = 0.0

    # The song title has to be in the video title...
    wanted = set(core_title(track.name).split())
    if wanted:
        coverage = len(wanted & set(title.split())) / len(wanted)
        points += 30 * coverage
        if coverage < 0.5:
            points -= 50

    # ...and the video title shouldn't name much else: "Love" is not "Love Story".
    extra = _unexplained(video.title, name, artists)
    points += 10 - 40 * extra
    if extra >= 0.5:
        points -= 50

    # "Pt. 1" is not "Pt. 2".
    artist_words = {word for artist in artists for word in artist.split()}
    if _numbers(track.name, artist_words) != _numbers(video.title, artist_words):
        points -= 80

    points += _artist_points(track, video)

    if video.duration_s is not None and track.duration_ms:
        length = track.duration_ms / 1000
        difference = abs(video.duration_s - length)
        if difference <= 3:
            points += 30
        elif difference <= 10:
            points += 15
        elif difference > length / 2:
            points -= 30
        elif difference > 30:
            # Music videos often run longer than the song.
            points -= 10

    for words in OTHER_VERSIONS:
        if _contains(title, words) != _contains(name, words):
            points -= 80

    # The search's own ranking breaks ties.
    return points - rank


@lru_cache(maxsize=4096)
def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: "Beyoncé - Topic" -> "beyonce topic"."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    return " ".join(re.findall(r"\w+", text))


@lru_cache(maxsize=4096)
def core_title(name: str) -> str:
    """The title without version info: "Song (feat. X) - Remastered 2011" -> "song"."""
    core = re.split(r"\s+-\s+", name, maxsplit=1)[0]
    return normalize(BRACKETED.sub(" ", core)) or normalize(name)


def _best(scored: Iterable[tuple[float, T]]) -> T | None:
    points, best = max(scored, key=lambda pair: pair[0], default=(0.0, None))
    return best if points >= MIN_SCORE else None


def _artist_points(track: Track, video: Video) -> int:
    title = normalize(video.title)
    channel = normalize(video.channel)
    artists = [a for a in (normalize(artist) for artist in track.artists) if a]
    if any(channel == f"{artist} topic" for artist in artists):
        # "Artist - Topic" channels hold the auto-generated album tracks, which
        # show up as proper songs in YouTube Music.
        return 35
    if any(_mentions(title, channel, artist) for artist in artists):
        return 15
    return -20


def _numbers(title: str, ignored: set[str]) -> set[int]:
    """Numbers that tell songs apart, as in "Pt. 2" or "No. 5".

    Years and credited artists ("feat. 21 Savage") don't count.
    """
    words = normalize(FEATURING.sub("", BRACKETED_NOISE.sub(" ", title))).split()
    return {int(w) for w in words if w.isdigit() and len(w) < 4 and w not in ignored}


def _unexplained(title: str, name: str, artists: list[str]) -> float:
    """The share of the video title's words that aren't in the track name.

    Brackets ("(Official Video)", "(From the Motion Picture ...)"), featured
    and other artists, filler, years and single letters don't count.
    """
    ignored = FILLER_WORDS.union(*(artist.split() for artist in artists))
    words = [
        word
        for word in normalize(FEATURING.sub("", BRACKETED.sub(" ", title))).split()
        if len(word) > 1 and not word.isdigit() and word not in ignored
    ]
    if not words:
        return 0.0
    in_name = set(name.split())
    return sum(word not in in_name for word in words) / len(words)


def _mentions(title: str, channel: str, artist: str) -> bool:
    if _contains(title, artist) or _contains(channel, artist):
        return True
    # Channels like "QueenVEVO". Too loose for very short names.
    return len(artist) >= 4 and artist.replace(" ", "") in channel.replace(" ", "")


def _contains(text: str, words: str) -> bool:
    return f" {words} " in f" {text} "

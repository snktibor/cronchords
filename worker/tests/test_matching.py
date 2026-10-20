import pytest

from cronchords.matching import best_match, core_title, normalize, search_query
from cronchords.spotify import Track
from cronchords.youtube import Video

RHAPSODY = Track(
    id="sp1",
    isrc="GBUM71029604",
    name="Bohemian Rhapsody - Remastered 2011",
    artists=("Queen",),
    duration_ms=354_000,
)


def video(title: str, channel: str, duration_s: int | None = 354, video_id: str = "") -> Video:
    return Video(id=video_id or title, title=title, channel=channel, duration_s=duration_s)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Beyoncé - Topic", "beyonce topic"),
        ("AC/DC", "ac dc"),
        ("Simon & Garfunkel", "simon and garfunkel"),
        ("  Guns N' Roses!! ", "guns n roses"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
        ("Stay (feat. Justin Bieber)", "stay"),
        ("(I Can't Get No) Satisfaction", "satisfaction"),
        ("(Untitled)", "untitled"),
    ],
)
def test_core_title(name, expected):
    assert core_title(name) == expected


def test_search_query():
    assert search_query(RHAPSODY) == "Queen - Bohemian Rhapsody - Remastered 2011"


def test_prefers_the_topic_upload():
    candidates = [
        video("Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official", 359),
        video("Queen - Bohemian Rhapsody (Lyrics)", "7clouds", 355),
        video("Bohemian Rhapsody (Remastered 2011)", "Queen - Topic", 355),
    ]

    assert best_match(RHAPSODY, candidates).channel == "Queen - Topic"


def test_takes_the_official_video_when_there_is_no_topic_upload():
    candidates = [
        video("Bohemian Rhapsody - Queen (Piano Cover)", "Some Pianist", 352),
        video("Queen – Bohemian Rhapsody (Official Video Remastered)", "QueenVEVO", 359),
    ]

    assert best_match(RHAPSODY, candidates).channel == "QueenVEVO"


@pytest.mark.parametrize(
    "title",
    [
        "Queen - Bohemian Rhapsody (Live Aid 1985)",
        "Bohemian Rhapsody | Karaoke Version",
        "Queen - Bohemian Rhapsody (Nightcore)",
        "Bohemian Rhapsody (cover by a random band)",
    ],
)
def test_rejects_other_versions(title):
    assert best_match(RHAPSODY, [video(title, "Somebody")]) is None


def test_keeps_live_versions_when_the_spotify_track_is_live():
    live = Track("sp2", "GBUM70000000", "Bohemian Rhapsody - Live Aid", ("Queen",), 360_000)

    match = best_match(live, [video("Queen - Bohemian Rhapsody (Live Aid 1985)", "Queen", 361)])

    assert match is not None


def test_rejects_another_song_from_the_same_artist():
    candidates = [video("Don't Stop Me Now (Remastered 2011)", "Queen - Topic", 354)]

    assert best_match(RHAPSODY, candidates) is None


def test_rejects_a_wildly_different_length():
    candidates = [video("Queen - Bohemian Rhapsody (extended mix)", "Fan uploads", 1200)]

    assert best_match(RHAPSODY, candidates) is None


def test_works_without_durations():
    candidates = [video("Bohemian Rhapsody (Remastered 2011)", "Queen - Topic", None)]

    assert best_match(RHAPSODY, candidates) is not None


def test_no_candidates():
    assert best_match(RHAPSODY, []) is None

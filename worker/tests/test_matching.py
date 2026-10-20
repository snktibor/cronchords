import pytest

from cronchords.matching import (
    TrackFinder,
    best_track,
    best_video,
    core_title,
    normalize,
    spotify_query,
    youtube_query,
)
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


def test_youtube_query():
    assert youtube_query(RHAPSODY) == "Queen - Bohemian Rhapsody - Remastered 2011"


def test_prefers_the_topic_upload():
    candidates = [
        video("Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official", 359),
        video("Queen - Bohemian Rhapsody (Lyrics)", "7clouds", 355),
        video("Bohemian Rhapsody (Remastered 2011)", "Queen - Topic", 355),
    ]

    assert best_video(RHAPSODY, candidates).channel == "Queen - Topic"


def test_takes_the_official_video_when_there_is_no_topic_upload():
    candidates = [
        video("Bohemian Rhapsody - Queen (Piano Cover)", "Some Pianist", 352),
        video("Queen – Bohemian Rhapsody (Official Video Remastered)", "QueenVEVO", 359),
    ]

    assert best_video(RHAPSODY, candidates).channel == "QueenVEVO"


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
    assert best_video(RHAPSODY, [video(title, "Somebody")]) is None


def test_keeps_live_versions_when_the_spotify_track_is_live():
    live = Track("sp2", "GBUM70000000", "Bohemian Rhapsody - Live Aid", ("Queen",), 360_000)

    match = best_video(live, [video("Queen - Bohemian Rhapsody (Live Aid 1985)", "Queen", 361)])

    assert match is not None


def test_rejects_another_song_from_the_same_artist():
    candidates = [video("Don't Stop Me Now (Remastered 2011)", "Queen - Topic", 354)]

    assert best_video(RHAPSODY, candidates) is None


def test_rejects_a_wildly_different_length():
    candidates = [video("Queen - Bohemian Rhapsody (extended mix)", "Fan uploads", 1200)]

    assert best_video(RHAPSODY, candidates) is None


def test_works_without_durations():
    candidates = [video("Bohemian Rhapsody (Remastered 2011)", "Queen - Topic", None)]

    assert best_video(RHAPSODY, candidates) is not None


def test_no_candidates():
    assert best_video(RHAPSODY, []) is None


# YouTube -> Spotify

GET_LUCKY = Track(
    id="sp3",
    isrc="USQX91300108",
    name="Get Lucky (feat. Pharrell Williams and Nile Rodgers)",
    artists=("Daft Punk", "Pharrell Williams", "Nile Rodgers"),
    duration_ms=369_000,
)
GET_LUCKY_RADIO_EDIT = Track(
    id="sp4",
    isrc="USQX91300105",
    name="Get Lucky (Radio Edit) [feat. Pharrell Williams and Nile Rodgers]",
    artists=("Daft Punk", "Pharrell Williams", "Nile Rodgers"),
    duration_ms=248_000,
)
BLINDING_LIGHTS = Track("sp5", "USUG11904206", "Blinding Lights", ("The Weeknd",), 200_000)


@pytest.mark.parametrize(
    ("title", "channel", "query"),
    [
        (
            "Get Lucky (feat. Pharrell Williams & Nile Rodgers) [Radio Edit]",
            "Daft Punk - Topic",
            "Get Lucky [Radio Edit] Daft Punk",
        ),
        (
            "Daft Punk - Get Lucky (Official Audio) ft. Pharrell Williams, Nile Rodgers",
            "Daft Punk",
            "Daft Punk Get Lucky",
        ),
        ("Blinding Lights (Official Video)", "TheWeekndVEVO", "Blinding Lights TheWeeknd"),
        (
            "Queen – Bohemian Rhapsody [Official Video Remastered]",
            "Queen Official",
            "Queen Bohemian Rhapsody",
        ),
    ],
)
def test_spotify_query(title, channel, query):
    assert spotify_query(video(title, channel)) == query


def test_picks_the_release_with_the_same_length():
    topic_upload = video("Get Lucky (Radio Edit)", "Daft Punk - Topic", 248)

    assert best_track(topic_upload, [GET_LUCKY, GET_LUCKY_RADIO_EDIT]) is GET_LUCKY_RADIO_EDIT


def test_accepts_a_music_video_that_runs_longer_than_the_song():
    music_video = video("The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 262)

    assert best_track(music_video, [BLINDING_LIGHTS]) is BLINDING_LIGHTS


def test_rejects_a_karaoke_track_for_the_original():
    karaoke = Track("sp6", None, "Blinding Lights (Karaoke Version)", ("Karaoke Hits",), 200_000)

    assert best_track(video("Blinding Lights", "The Weeknd - Topic", 200), [karaoke]) is None


def test_rejects_a_live_track_for_the_studio_recording():
    live = Track("sp7", None, "Bohemian Rhapsody - Live Aid", ("Queen",), 354_000)

    assert best_track(video("Bohemian Rhapsody (Remastered 2011)", "Queen - Topic"), [live]) is None


def test_rejects_a_song_with_a_longer_title():
    love = Track("sp8", None, "Love", ("Taylor Swift",), 235_000)

    assert (
        best_track(video("Love Story (Taylor's Version)", "Taylor Swift - Topic", 235), [love])
        is None
    )


def test_finds_a_video_among_the_tracks_of_a_playlist():
    finder = TrackFinder([RHAPSODY, BLINDING_LIGHTS, GET_LUCKY])

    found = finder.find(video("Queen - Bohemian Rhapsody (Official Video)", "Queen Official", 359))

    assert found is RHAPSODY
    assert finder.find(video("Daft Punk - Around the World", "Daft Punk", 429)) is None


def test_a_song_with_the_same_title_by_someone_else_is_not_found_in_a_playlist():
    the_xx = Track("sp9", None, "Intro", ("The xx",), 127_000)

    assert TrackFinder([the_xx]).find(video("Intro", "M83 - Topic", 129)) is None


def test_the_part_number_has_to_match():
    part_1 = Track("sp10", None, "Another Brick in the Wall, Pt. 1", ("Pink Floyd",), 191_000)
    part_2 = Track("sp11", None, "Another Brick in the Wall, Pt. 2", ("Pink Floyd",), 239_000)
    music_video = video(
        "Pink Floyd - Another Brick In The Wall (Part 2) [Official Video]", "Pink Floyd", 239
    )

    assert (
        best_track(video("Another Brick in the Wall, Pt. 2", "Pink Floyd - Topic", 191), [part_1])
        is None
    )
    assert best_track(music_video, [part_1, part_2]) is part_2


def test_numbers_in_artist_names_and_years_are_not_part_numbers():
    sugar = Track("sp12", None, "Sugar", ("Maroon 5",), 235_000)

    assert best_track(
        video("Maroon 5 - Sugar (Official Music Video) 2015", "Maroon5VEVO", 301), [sugar]
    )

from datetime import timedelta

import pytest
from fake_cloud import NOW, FakeCloud, FakeVideo, sync
from songs import DREAMS, GET_LUCKY, HEY_JUDE, RHAPSODY, stock

from cronchords.matching import spotify_query


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    stock(cloud, GET_LUCKY, RHAPSODY, DREAMS, HEY_JUDE)
    cloud.spotify_playlists["road-trip"] = {"snapshot_id": "snap-1", "entries": []}
    cloud.add_mapping("road-trip", "yt-road-trip", direction="youtube_to_spotify")
    cloud.youtube_playlists["yt-road-trip"] = ["lucky", "dreams"]
    return cloud


def test_videos_are_added_to_spotify(cloud):
    assert sync(cloud) == 0

    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]
    assert cloud.spotify_searches() == [spotify_query(GET_LUCKY.video), spotify_query(DREAMS.video)]
    assert cloud.calls("www.googleapis.com", "/youtube/v3/search") == []
    assert cloud.video_cache["lucky"]["spotify_track_id"] == GET_LUCKY.track_id
    mapping = cloud.mappings[0]
    assert mapping["last_error"] is None
    assert mapping["synced_songs"] == []


def test_matches_also_spare_youtube_searches(cloud):
    sync(cloud)
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [DREAMS.entry]}
    cloud.add_mapping("favourites", "yt-favourites")
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert cloud.calls("www.googleapis.com", "/youtube/v3/search") == []
    assert cloud.youtube_playlists["yt-favourites"] == ["dreams"]


def test_unchanged_playlists_cost_one_youtube_unit(cloud):
    sync(cloud)
    # Adding the tracks changed the Spotify playlist, the next run checks it once more.
    sync(cloud)
    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert len(cloud.calls("www.googleapis.com")) == 1
    assert cloud.calls("api.spotify.com", "/v1/playlists/road-trip/items") == []
    assert cloud.mappings[0]["last_error"] is None


def test_songs_already_on_spotify_are_not_added_again(cloud):
    other_release = GET_LUCKY.entry["item"] | {"id": "lucky-from-a-compilation"}
    cloud.spotify_playlists["road-trip"]["entries"] = [{"is_local": False, "item": other_release}]

    assert sync(cloud) == 0

    assert cloud.spotify_track_ids("road-trip") == ["lucky-from-a-compilation", DREAMS.track_id]
    assert cloud.spotify_searches() == [spotify_query(DREAMS.video)]


def test_videos_matched_for_spotify_tracks_are_not_searched(cloud):
    for song in (GET_LUCKY, DREAMS):
        cloud.cache[song.isrc] = {
            "isrc": song.isrc,
            "spotify_track_id": song.track_id,
            "youtube_video_id": song.video_id,
            "searched_at": NOW.isoformat(),
        }

    assert sync(cloud) == 0

    assert cloud.spotify_searches() == []
    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]


def test_unmatched_videos_are_searched_again_after_a_while(cloud):
    cloud.videos["bootleg"] = FakeVideo("Untitled demo", "Someone's Channel", "PT2M")
    cloud.youtube_playlists["yt-road-trip"] = ["bootleg"]
    sync(cloud)
    assert cloud.video_cache["bootleg"]["spotify_track_id"] is None
    assert cloud.mappings[0]["last_error"] is None

    cloud.youtube_playlists["yt-road-trip"].append("jude")
    cloud.requests.clear()
    sync(cloud, now=NOW + timedelta(days=1))
    assert cloud.spotify_searches() == [spotify_query(HEY_JUDE.video)]

    cloud.youtube_playlists["yt-road-trip"].append("rhapsody")
    cloud.requests.clear()
    sync(cloud, now=NOW + timedelta(days=31))
    assert cloud.spotify_searches() == [
        "Untitled demo Someone's Channel",
        spotify_query(RHAPSODY.video),
    ]


def test_deleted_videos_are_skipped(cloud):
    cloud.videos["lucky"].removed = True

    assert sync(cloud) == 0

    assert cloud.spotify_searches() == [spotify_query(DREAMS.video)]
    assert cloud.spotify_track_ids("road-trip") == [DREAMS.track_id]


def test_missing_permission_to_edit_spotify_playlists(cloud):
    cloud.spotify_can_edit = False

    assert sync(cloud) == 1

    assert "Reconnect Spotify" in cloud.mappings[0]["last_error"]


def test_spotify_rate_limit_stops_the_run(cloud):
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": []}
    cloud.add_mapping("favourites", "yt-favourites", direction="youtube_to_spotify")
    cloud.spotify_rate_limited = True

    assert sync(cloud) == 0

    road_trip, favourites = cloud.mappings
    assert road_trip["last_error"] == "Waiting for Spotify's rate limit to pass"
    assert favourites["last_error"] is None
    assert len(cloud.calls("api.spotify.com")) == 1


def test_a_match_corrected_by_hand_wins(cloud):
    # An earlier run took a cover for the song, then the video's entry was fixed.
    cloud.cache["QZCOVER00001"] = {
        "isrc": "QZCOVER00001",
        "spotify_track_id": "dreams-cover",
        "youtube_video_id": "dreams",
        "searched_at": NOW.isoformat(),
    }
    cloud.video_cache["dreams"] = {
        "youtube_video_id": "dreams",
        "spotify_track_id": DREAMS.track_id,
        "isrc": DREAMS.isrc,
        "searched_at": NOW.isoformat(),
    }

    assert sync(cloud) == 0

    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]

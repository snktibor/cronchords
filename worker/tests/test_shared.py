import pytest
from fake_cloud import FakeCloud, FakeVideo, sync
from songs import DREAMS, GET_LUCKY, HEY_JUDE, RHAPSODY, stock

from cronchords.matching import spotify_query, youtube_query

COVER = "lucky-cover"


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    stock(cloud, GET_LUCKY, RHAPSODY, DREAMS, HEY_JUDE)
    cloud.videos[COVER] = FakeVideo("Get Lucky (Acoustic Cover)", "Some Guy", "PT4M2S")
    cloud.spotify_playlists["road-trip"] = {"snapshot_id": "snap-1", "entries": [GET_LUCKY.entry]}
    cloud.add_mapping("road-trip", "yt-road-trip")
    return cloud


def youtube_to_spotify(cloud: FakeCloud) -> None:
    cloud.mappings[0]["direction"] = "youtube_to_spotify"
    cloud.spotify_playlists["road-trip"]["entries"] = []
    cloud.youtube_playlists["yt-road-trip"] = ["lucky"]


def test_a_shared_match_spares_the_youtube_search(cloud):
    cloud.share(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == []
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]
    assert cloud.cache[GET_LUCKY.isrc]["youtube_video_id"] == "lucky"
    assert cloud.cache[GET_LUCKY.isrc]["source"] == "shared"


def test_shared_matches_that_fail_the_matching_rules_are_searched_for(cloud):
    cloud.share(GET_LUCKY, "dreams")

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == [youtube_query(GET_LUCKY.track)]
    assert cloud.cache[GET_LUCKY.isrc]["youtube_video_id"] == "lucky"
    assert cloud.cache[GET_LUCKY.isrc]["source"] == "search"


def test_matches_people_picked_only_need_the_songs_title(cloud):
    cloud.share(GET_LUCKY, COVER, source="corrected")

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == []
    assert cloud.youtube_playlists["yt-road-trip"] == [COVER]


def test_matches_the_rules_found_need_to_pass_them(cloud):
    cloud.share(GET_LUCKY, COVER, source="matched")

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]


def test_the_next_shared_match_is_tried_when_the_best_is_gone(cloud):
    cloud.share(GET_LUCKY, "deleted-video")
    cloud.share(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == []
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]


def test_the_cache_comes_before_the_shared_list(cloud):
    sync(cloud)
    cloud.share(GET_LUCKY, COVER, source="reviewed")
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [GET_LUCKY.entry]}
    cloud.add_mapping("favourites", "yt-favourites")
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-favourites"] == ["lucky"]
    assert cloud.calls("raw.githubusercontent.com") == []


def test_shared_matches_work_after_the_search_limit_is_reached(cloud):
    cloud.searches_left = 0
    cloud.share(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]
    assert cloud.mappings[0]["last_error"] is None


def test_a_shared_match_spares_the_spotify_search(cloud):
    youtube_to_spotify(cloud)
    cloud.share(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.spotify_searches() == []
    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id]
    assert cloud.video_cache["lucky"]["source"] == "shared"
    assert cloud.cache[GET_LUCKY.isrc]["source"] == "shared"


def test_shared_tracks_that_fail_the_matching_rules_are_searched_for(cloud):
    youtube_to_spotify(cloud)
    cloud.shared_videos["lucky"] = [{"track": DREAMS.track_id, "source": "matched", "votes": 3}]

    assert sync(cloud) == 0

    assert cloud.spotify_searches() == [spotify_query(GET_LUCKY.video)]
    assert cloud.spotify_track_ids("road-trip") == [GET_LUCKY.track_id]


def test_songs_are_searched_for_when_the_list_cant_be_read(cloud, caplog):
    cloud.share(GET_LUCKY)
    cloud.shared_down = True

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]
    assert len(cloud.calls("raw.githubusercontent.com")) == 1
    assert "can't be read (HTTP 403)" in caplog.text


def test_a_list_in_a_newer_format_is_left_alone(cloud, caplog):
    cloud.share(GET_LUCKY)
    cloud.shared_version = 2

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == [youtube_query(GET_LUCKY.track)]
    assert "Update cronchords to use it again" in caplog.text


def test_entries_that_dont_make_sense_are_skipped(cloud):
    cloud.shared_isrcs[GET_LUCKY.isrc] = [
        "not a row",
        {"track": GET_LUCKY.track_id, "video": "../../etc", "source": "matched"},
        {"track": GET_LUCKY.track_id, "video": "lucky", "source": "trust me"},
    ]

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == [youtube_query(GET_LUCKY.track)]
    # Only the search results' lengths were looked up, no shared video.
    lookups = cloud.calls("www.googleapis.com", "/youtube/v3/videos")
    assert [r.url.params["part"] for r in lookups] == ["contentDetails"]


def test_the_shared_list_can_be_turned_off(cloud, monkeypatch):
    monkeypatch.setenv("USE_SHARED_MATCHES", "false")
    cloud.share(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == [youtube_query(GET_LUCKY.track)]
    assert cloud.calls("raw.githubusercontent.com") == []

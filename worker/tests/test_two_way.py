import pytest
from fake_cloud import FakeCloud, FakeVideo, sync
from songs import DREAMS, GET_LUCKY, HEY_JUDE, RHAPSODY, stock

from cronchords.matching import spotify_query


def changes(cloud: FakeCloud) -> list[str]:
    """The playlist edits the worker made."""
    return [
        f"{r.method} {r.url.host}{r.url.path}"
        for r in cloud.requests
        if r.method in ("POST", "DELETE")
        and r.url.host in ("api.spotify.com", "www.googleapis.com")
    ]


def change_playlist(cloud: FakeCloud, playlist_id: str, entries: list[dict]) -> None:
    cloud.spotify_playlists[playlist_id] = {
        "snapshot_id": cloud.spotify_playlists[playlist_id]["snapshot_id"] + "+",
        "entries": entries,
    }


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    stock(cloud, GET_LUCKY, RHAPSODY, DREAMS, HEY_JUDE)
    cloud.spotify_playlists["road-trip"] = {
        "snapshot_id": "snap-1",
        "entries": [GET_LUCKY.entry, RHAPSODY.entry],
    }
    cloud.add_mapping("road-trip", "yt-road-trip", direction="both")
    cloud.youtube_playlists["yt-road-trip"] = ["rhapsody", "dreams"]
    return cloud


@pytest.fixture
def synced(cloud) -> FakeCloud:
    assert sync(cloud) == 0
    cloud.requests.clear()
    return cloud


def test_first_sync_merges_both_playlists(cloud):
    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["rhapsody", "dreams", "lucky"]
    assert cloud.spotify_track_ids("road-trip") == [
        GET_LUCKY.track_id,
        RHAPSODY.track_id,
        DREAMS.track_id,
    ]
    # Bohemian Rhapsody was on both sides already, so nobody looked for it.
    assert cloud.youtube_searches() == ["Daft Punk - Get Lucky"]
    assert cloud.spotify_searches() == [spotify_query(DREAMS.video)]
    mapping = cloud.mappings[0]
    assert mapping["synced_songs"] == sorted(
        [[s.track_id, s.video_id] for s in (GET_LUCKY, RHAPSODY, DREAMS)]
    )
    assert mapping["last_error"] is None


def test_in_sync_after_one_more_check(synced):
    # The first sync changed both playlists, so the second one looks again.
    assert sync(synced) == 0
    assert changes(synced) == []
    synced.requests.clear()

    assert sync(synced) == 0

    assert synced.calls("api.spotify.com", "/v1/playlists/road-trip/items") == []
    assert len(synced.calls("www.googleapis.com")) == 1


def test_songs_removed_on_spotify_are_removed_from_youtube(synced):
    change_playlist(synced, "road-trip", [GET_LUCKY.entry, DREAMS.entry])

    assert sync(synced) == 0

    assert synced.youtube_playlists["yt-road-trip"] == ["dreams", "lucky"]
    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]
    assert [RHAPSODY.track_id, RHAPSODY.video_id] not in synced.mappings[0]["synced_songs"]


def test_songs_removed_on_youtube_are_removed_from_spotify(synced):
    synced.youtube_playlists["yt-road-trip"].remove("dreams")

    assert sync(synced) == 0

    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, RHAPSODY.track_id]
    assert synced.youtube_playlists["yt-road-trip"] == ["rhapsody", "lucky"]


def test_removed_songs_stay_removed(synced):
    synced.youtube_playlists["yt-road-trip"].remove("dreams")
    change_playlist(synced, "road-trip", [GET_LUCKY.entry, DREAMS.entry])
    sync(synced)

    sync(synced)
    sync(synced)

    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id]
    assert synced.youtube_playlists["yt-road-trip"] == ["lucky"]
    assert synced.youtube_searches() == synced.spotify_searches() == []


def test_songs_added_on_both_sides_are_paired_up(synced):
    synced.videos["jude-video"] = FakeVideo("The Beatles - Hey Jude", "The Beatles", "PT7M24S")
    synced.youtube_playlists["yt-road-trip"].append("jude-video")
    entries = synced.spotify_playlists["road-trip"]["entries"]
    change_playlist(synced, "road-trip", [*entries, HEY_JUDE.entry])

    assert sync(synced) == 0

    assert changes(synced) == []
    assert synced.youtube_searches() == synced.spotify_searches() == []
    assert [HEY_JUDE.track_id, "jude-video"] in synced.mappings[0]["synced_songs"]


def test_replacing_a_wrongly_matched_video_keeps_the_song(synced):
    synced.videos["lucky-audio"] = FakeVideo(
        "Daft Punk - Get Lucky (Official Audio) ft. Pharrell Williams, Nile Rodgers",
        "Daft Punk",
        "PT4M8S",
    )
    playlist = synced.youtube_playlists["yt-road-trip"]
    playlist[playlist.index("lucky")] = "lucky-audio"

    assert sync(synced) == 0

    assert changes(synced) == []
    assert synced.spotify_track_ids("road-trip") == [
        GET_LUCKY.track_id,
        RHAPSODY.track_id,
        DREAMS.track_id,
    ]
    assert [GET_LUCKY.track_id, "lucky-audio"] in synced.mappings[0]["synced_songs"]


def test_songs_without_a_match_stay_where_they_are(synced):
    synced.videos["bootleg"] = FakeVideo("Untitled demo", "Someone's Channel", "PT2M")
    synced.youtube_playlists["yt-road-trip"].append("bootleg")
    sync(synced)

    synced.youtube_playlists["yt-road-trip"].remove("dreams")
    sync(synced)

    assert "bootleg" in synced.youtube_playlists["yt-road-trip"]
    assert DREAMS.track_id not in synced.spotify_track_ids("road-trip")
    assert "bootleg" not in {video for _, video in synced.mappings[0]["synced_songs"]}


def test_an_empty_playlist_does_not_empty_the_other_side(synced):
    synced.youtube_playlists["yt-road-trip"].clear()

    assert sync(synced) == 0

    mapping = synced.mappings[0]
    assert len(synced.spotify_track_ids("road-trip")) == 3
    assert "came back empty" in mapping["last_error"]
    assert len(mapping["synced_songs"]) == 3

    # Once there's something on YouTube again, the removals go through.
    synced.youtube_playlists["yt-road-trip"].append("jude")
    sync(synced)
    assert synced.spotify_track_ids("road-trip") == [HEY_JUDE.track_id]
    assert mapping["last_error"] is None


def test_running_out_of_quota_halfway_is_finished_later(synced):
    change_playlist(synced, "road-trip", [DREAMS.entry])
    # Enough to list the playlist, look up the two videos and remove one of them.
    synced.units_left = 1 + 1 + 50

    assert sync(synced) == 0
    assert synced.mappings[0]["last_error"] == "Waiting for the YouTube API quota to reset"
    assert len(synced.youtube_playlists["yt-road-trip"]) == 2

    synced.units_left = 10_000
    assert sync(synced) == 0

    assert synced.youtube_playlists["yt-road-trip"] == ["dreams"]
    assert synced.mappings[0]["synced_songs"] == [[DREAMS.track_id, DREAMS.video_id]]


def test_an_empty_youtube_playlist_is_not_trusted_even_with_new_songs(synced):
    synced.youtube_playlists["yt-road-trip"].clear()
    entries = synced.spotify_playlists["road-trip"]["entries"]
    change_playlist(synced, "road-trip", [*entries, HEY_JUDE.entry])

    assert sync(synced) == 0

    assert len(synced.spotify_track_ids("road-trip")) == 4
    assert synced.youtube_playlists["yt-road-trip"] == ["jude"]
    assert "came back empty" in synced.mappings[0]["last_error"]


def test_a_failed_run_still_remembers_what_it_did(synced):
    synced.youtube_playlists["yt-road-trip"].remove("dreams")
    change_playlist(synced, "road-trip", [GET_LUCKY.entry, DREAMS.entry])
    # Enough to list the playlist twice and look up the orphaned video, but
    # not to remove Bohemian Rhapsody from YouTube.
    synced.units_left = 1 + 1 + 1
    sync(synced)
    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id]
    assert synced.mappings[0]["last_error"] == "Waiting for the YouTube API quota to reset"

    # Changed their mind about Dreams.
    synced.youtube_playlists["yt-road-trip"].append("dreams")
    synced.units_left = 10_000
    sync(synced)

    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]
    assert synced.youtube_playlists["yt-road-trip"] == ["lucky", "dreams"]


def test_a_spotify_playlist_edited_while_being_read_is_left_for_the_next_run(synced):
    def edit():
        synced.while_reading_spotify = None
        change_playlist(synced, "road-trip", [GET_LUCKY.entry])

    change_playlist(synced, "road-trip", [GET_LUCKY.entry, RHAPSODY.entry])
    synced.while_reading_spotify = edit

    assert sync(synced) == 0
    assert changes(synced) == []

    assert sync(synced) == 0
    assert synced.youtube_playlists["yt-road-trip"] == ["lucky"]


def test_a_video_skipped_by_paging_does_not_count_as_removed(synced):
    synced.skip_once = {"dreams"}

    assert sync(synced) == 0

    assert synced.spotify_track_ids("road-trip") == [
        GET_LUCKY.track_id,
        RHAPSODY.track_id,
        DREAMS.track_id,
    ]
    assert [DREAMS.track_id, "dreams"] in synced.mappings[0]["synced_songs"]


def test_undoing_an_addition_before_the_next_run_is_noticed(cloud):
    cloud.youtube_playlists["yt-road-trip"] = ["rhapsody"]
    sync(cloud)
    # Back to what the first run found, but Get Lucky was removed on YouTube.
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")

    assert sync(cloud) == 0

    assert GET_LUCKY.track_id not in cloud.spotify_track_ids("road-trip")


def test_a_dead_video_is_replaced_and_its_entry_can_be_cleaned_up(synced):
    sync(synced)  # until nothing changes anymore, see above
    synced.videos["dreams"].removed = True
    synced.add_video("Fleetwood Mac - Dreams", "dreams-again", "Dreams", "Fleetwood Mac - Topic")
    synced.search_results["Fleetwood Mac - Dreams"].remove("dreams")

    assert sync(synced) == 0
    assert synced.youtube_playlists["yt-road-trip"][-1] == "dreams-again"

    synced.youtube_playlists["yt-road-trip"].remove("dreams")
    assert sync(synced) == 0

    assert DREAMS.track_id in synced.spotify_track_ids("road-trip")


def test_the_same_recording_twice_is_searched_once(cloud):
    compilation = DREAMS.entry["item"] | {"id": "dreams-from-a-compilation"}
    entries = [DREAMS.entry, {"is_local": False, "item": compilation}]
    cloud.spotify_playlists["road-trip"] = {"snapshot_id": "snap-1", "entries": entries}
    cloud.youtube_playlists["yt-road-trip"] = []

    assert sync(cloud) == 0

    assert cloud.youtube_searches() == ["Fleetwood Mac - Dreams"]
    assert cloud.youtube_playlists["yt-road-trip"] == ["dreams"]

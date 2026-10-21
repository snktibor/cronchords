from dataclasses import replace
from datetime import timedelta

import pytest
from fake_cloud import NOW, FakeCloud, FakeVideo, spotify_entry, sync
from songs import DREAMS, GET_LUCKY, HEY_JUDE, RHAPSODY, stock

from cronchords.matching import spotify_query
from cronchords.supabase import Side
from cronchords.sync import Playlists
from cronchords.youtube import Video

LAST_WEEK = NOW - timedelta(days=7)


def changes(cloud: FakeCloud) -> list[str]:
    """The playlist edits the worker made."""
    return [
        f"{r.method} {r.url.host}{r.url.path}"
        for r in cloud.requests
        if r.method in ("POST", "DELETE")
        and r.url.host in ("api.spotify.com", "www.googleapis.com")
    ]


def pairs(cloud: FakeCloud) -> set[tuple[str, str]]:
    """The (track, video) pairs the mapping remembers."""
    return {(song["track"], song["video"]) for song in cloud.mappings[0]["synced_songs"]}


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
    # Long enough ago that the songs it copied can't be swapped for another match
    # anymore, as the runs since saw.
    assert sync(cloud, now=LAST_WEEK) == 0
    assert sync(cloud, now=NOW - timedelta(hours=1)) == 0
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
    assert pairs(cloud) == {(s.track_id, s.video_id) for s in (GET_LUCKY, RHAPSODY, DREAMS)}
    mapping = cloud.mappings[0]
    copied = {song["video"]: song.get("copied_to") for song in mapping["synced_songs"]}
    assert copied == {"lucky": "youtube", "rhapsody": None, "dreams": "spotify"}
    assert mapping["last_error"] is None


def test_in_sync_after_one_more_check(cloud):
    sync(cloud)
    cloud.requests.clear()
    # The first sync changed both playlists, so the second one looks again.
    assert sync(cloud) == 0
    assert changes(cloud) == []
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert cloud.calls("api.spotify.com", "/v1/playlists/road-trip/items") == []
    assert len(cloud.calls("www.googleapis.com")) == 1


def test_songs_removed_on_spotify_are_removed_from_youtube(synced):
    change_playlist(synced, "road-trip", [GET_LUCKY.entry, DREAMS.entry])

    assert sync(synced) == 0

    assert synced.youtube_playlists["yt-road-trip"] == ["dreams", "lucky"]
    assert synced.spotify_track_ids("road-trip") == [GET_LUCKY.track_id, DREAMS.track_id]
    assert (RHAPSODY.track_id, RHAPSODY.video_id) not in pairs(synced)


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
    assert (HEY_JUDE.track_id, "jude-video") in pairs(synced)


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
    assert (GET_LUCKY.track_id, "lucky-audio") in pairs(synced)


def test_songs_without_a_match_stay_where_they_are(synced):
    synced.videos["bootleg"] = FakeVideo("Untitled demo", "Someone's Channel", "PT2M")
    synced.youtube_playlists["yt-road-trip"].append("bootleg")
    sync(synced)

    synced.youtube_playlists["yt-road-trip"].remove("dreams")
    sync(synced)

    assert "bootleg" in synced.youtube_playlists["yt-road-trip"]
    assert DREAMS.track_id not in synced.spotify_track_ids("road-trip")
    assert "bootleg" not in {video for _, video in pairs(synced)}


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
    assert pairs(synced) == {(DREAMS.track_id, DREAMS.video_id)}


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
    assert (DREAMS.track_id, "dreams") in pairs(synced)


def test_undoing_an_addition_before_the_next_run_is_noticed(cloud):
    cloud.youtube_playlists["yt-road-trip"] = ["rhapsody"]
    sync(cloud)
    # Back to what the first run found, but Get Lucky was removed on YouTube.
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")

    sync(cloud, now=NOW + timedelta(hours=1))
    assert sync(cloud, now=NOW + timedelta(hours=13)) == 0

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


# The first sync copied Get Lucky to YouTube and Dreams to Spotify. Those copies
# may turn out to be wrong matches, so removing one only takes effect once the
# worker has waited a while for the right song.


def test_removing_a_new_copy_does_not_remove_the_song_right_away(cloud):
    sync(cloud)
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")

    assert sync(cloud, now=NOW + timedelta(hours=1)) == 0

    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")
    [song] = [s for s in cloud.mappings[0]["synced_songs"] if s["track"] == GET_LUCKY.track_id]
    assert song["missing_since"] == (NOW + timedelta(hours=1)).isoformat()


def test_a_song_with_the_same_title_takes_the_place_of_a_removed_copy(cloud):
    sync(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=1))
    # Not a video the worker would have taken: the artist isn't named anywhere.
    cloud.videos["lucky-edit"] = FakeVideo("Get Lucky (Radio Edit)", "Columbia Records", "PT4M8S")
    playlist.append("lucky-edit")
    cloud.requests.clear()

    assert sync(cloud, now=NOW + timedelta(hours=11)) == 0
    assert sync(cloud, now=NOW + timedelta(hours=14)) == 0

    assert changes(cloud) == []
    assert cloud.spotify_searches() == cloud.youtube_searches() == []
    assert (GET_LUCKY.track_id, "lucky-edit") in pairs(cloud)
    assert (GET_LUCKY.track_id, "lucky") not in pairs(cloud)
    # Other playlists with the song get this video from now on.
    assert cloud.cache[GET_LUCKY.isrc]["youtube_video_id"] == "lucky-edit"


def test_a_replacement_can_be_replaced_too(cloud):
    sync(cloud)
    cloud.videos["lucky-edit"] = FakeVideo("Get Lucky (Radio Edit)", "Columbia Records", "PT4M8S")
    cloud.videos["lucky-live"] = FakeVideo("Get Lucky (Live)", "Daft Punk", "PT6M2S")
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist[playlist.index("lucky")] = "lucky-edit"
    sync(cloud, now=NOW + timedelta(hours=1))
    playlist[playlist.index("lucky-edit")] = "lucky-live"

    assert sync(cloud, now=NOW + timedelta(hours=30)) == 0

    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")
    assert (GET_LUCKY.track_id, "lucky-live") in pairs(cloud)


def test_a_track_copied_to_spotify_can_be_replaced_the_same_way(cloud):
    sync(cloud)
    remaster = spotify_entry(
        "USWB10400047", "Dreams - 2004 Remaster", "Fleetwood Mac", 257_000, "dreams-2004"
    )
    change_playlist(cloud, "road-trip", [GET_LUCKY.entry, RHAPSODY.entry, remaster])
    cloud.requests.clear()

    assert sync(cloud, now=NOW + timedelta(hours=1)) == 0

    assert changes(cloud) == []
    assert cloud.youtube_searches() == []
    assert ("dreams-2004", "dreams") in pairs(cloud)
    assert cloud.video_cache["dreams"]["spotify_track_id"] == "dreams-2004"


def test_without_a_replacement_the_song_is_removed_after_the_wait(cloud):
    sync(cloud)
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=1))
    sync(cloud, now=NOW + timedelta(hours=12))
    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")

    # Nothing changed since, but the wait is over.
    assert sync(cloud, now=NOW + timedelta(hours=13)) == 0

    assert GET_LUCKY.track_id not in cloud.spotify_track_ids("road-trip")
    assert GET_LUCKY.track_id not in {track for track, _ in pairs(cloud)}


def test_a_song_with_another_title_is_not_taken_for_a_replacement(cloud):
    sync(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    playlist.append("jude")

    assert sync(cloud, now=NOW + timedelta(hours=1)) == 0
    assert cloud.spotify_track_ids("road-trip") == [
        GET_LUCKY.track_id,
        RHAPSODY.track_id,
        DREAMS.track_id,
        HEY_JUDE.track_id,
    ]

    sync(cloud, now=NOW + timedelta(hours=13))
    assert GET_LUCKY.track_id not in cloud.spotify_track_ids("road-trip")


def test_putting_a_removed_copy_back_ends_the_wait(cloud):
    sync(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=1))
    playlist.append("lucky")
    cloud.requests.clear()

    assert sync(cloud, now=NOW + timedelta(hours=2)) == 0
    assert sync(cloud, now=NOW + timedelta(hours=14)) == 0

    assert changes(cloud) == []
    assert not any("missing_since" in song for song in cloud.mappings[0]["synced_songs"])


def test_removing_the_song_a_copy_was_made_of_goes_through_right_away(cloud):
    sync(cloud)
    change_playlist(cloud, "road-trip", [RHAPSODY.entry, DREAMS.entry])

    assert sync(cloud, now=NOW + timedelta(hours=1)) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["rhapsody", "dreams"]


def test_copies_older_than_the_correction_window_are_removed_right_away(synced):
    synced.youtube_playlists["yt-road-trip"].remove("lucky")

    assert sync(synced) == 0

    assert GET_LUCKY.track_id not in synced.spotify_track_ids("road-trip")


def test_a_better_match_for_a_removed_copy_takes_its_place(cloud):
    wrong = spotify_entry("USX111111111", "New York, New York", "Frank Sinatra", 206_000, "ny")
    theme = spotify_entry(
        "USX222222222", "Theme From New York, New York", "Frank Sinatra", 206_000, "ny-theme"
    )
    cloud.videos["ny-video"] = FakeVideo("Frank Sinatra - New York, New York", "Frank Sinatra")
    cloud.youtube_playlists["yt-road-trip"].append("ny-video")
    title = cloud.videos["ny-video"].title
    cloud.add_track(spotify_query(Video("ny-video", title, "Frank Sinatra")), wrong)
    sync(cloud)
    assert "ny" in cloud.spotify_track_ids("road-trip")
    entries = cloud.spotify_playlists["road-trip"]["entries"]
    change_playlist(cloud, "road-trip", [*entries[:-1], theme])
    cloud.requests.clear()

    # Not the same title, but the video's best match.
    assert sync(cloud, now=NOW + timedelta(hours=1)) == 0

    assert changes(cloud) == []
    assert ("ny-theme", "ny-video") in pairs(cloud)
    assert cloud.video_cache["ny-video"]["spotify_track_id"] == "ny-theme"


def test_a_new_song_replaces_one_removed_copy_at_most(cloud):
    cranberries = spotify_entry("IEA009300001", "Dreams", "The Cranberries", 272_000, "cran")
    cloud.add_video("The Cranberries - Dreams", "cran-video", "Dreams", "The Cranberries - Topic")
    change_playlist(cloud, "road-trip", [DREAMS.entry, cranberries])
    cloud.youtube_playlists["yt-road-trip"] = []
    sync(cloud)
    # Neither video names its artist.
    cloud.videos["lyrics"] = FakeVideo("Dreams (Lyrics)", "7clouds", "PT4M17S")
    cloud.youtube_playlists["yt-road-trip"] = ["lyrics"]

    sync(cloud, now=NOW + timedelta(hours=1))
    sync(cloud, now=NOW + timedelta(hours=13))

    assert len(cloud.spotify_track_ids("road-trip")) == 1
    assert {video for _, video in pairs(cloud)} == {"lyrics"}


def test_a_copy_removed_just_before_the_window_closes_waits(cloud):
    sync(cloud)
    sync(cloud, now=NOW + timedelta(hours=71))
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")

    # It may have been removed within 72 hours of the copy, so it counts.
    assert sync(cloud, now=NOW + timedelta(hours=72)) == 0

    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")


def test_a_replacement_made_in_time_counts_even_if_the_runs_fail_meanwhile(cloud):
    sync(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=1))
    cloud.units_left = 0
    cloud.videos["lucky-edit"] = FakeVideo("Get Lucky (Radio Edit)", "Columbia Records", "PT4M8S")
    playlist.append("lucky-edit")
    assert sync(cloud, now=NOW + timedelta(hours=3)) == 0
    assert "quota" in cloud.mappings[0]["last_error"]
    cloud.units_left = 10_000

    assert sync(cloud, now=NOW + timedelta(hours=20)) == 0

    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")
    assert (GET_LUCKY.track_id, "lucky-edit") in pairs(cloud)


def test_a_track_paired_with_a_copy_later_waits_with_it(cloud):
    compilation = {"is_local": False, "item": GET_LUCKY.entry["item"] | {"id": "lucky-2"}}
    sync(cloud)
    entries = cloud.spotify_playlists["road-trip"]["entries"]
    change_playlist(cloud, "road-trip", [*entries, compilation])
    sync(cloud, now=NOW + timedelta(hours=1))
    assert ("lucky-2", "lucky") in pairs(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=2))
    cloud.videos["lucky-edit"] = FakeVideo("Get Lucky (Radio Edit)", "Columbia Records", "PT4M8S")
    playlist.append("lucky-edit")

    assert sync(cloud, now=NOW + timedelta(hours=3)) == 0

    assert {GET_LUCKY.track_id, "lucky-2"} <= set(cloud.spotify_track_ids("road-trip"))
    assert {(GET_LUCKY.track_id, "lucky-edit"), ("lucky-2", "lucky-edit")} <= pairs(cloud)


def test_a_song_that_was_there_before_the_copy_does_not_replace_it(cloud):
    # Live recordings don't match the studio one, so this one stays unpaired.
    cloud.videos["bootleg"] = FakeVideo("Get Lucky (Live Bootleg)", "Some Fan", "PT6M2S")
    cloud.youtube_playlists["yt-road-trip"].append("bootleg")
    sync(cloud)
    cloud.youtube_playlists["yt-road-trip"].remove("lucky")

    sync(cloud, now=NOW + timedelta(hours=1))
    sync(cloud, now=NOW + timedelta(hours=13))

    assert GET_LUCKY.track_id not in cloud.spotify_track_ids("road-trip")
    assert cloud.cache[GET_LUCKY.isrc]["youtube_video_id"] == "lucky"


def test_a_replacement_found_by_a_run_that_stops_halfway_still_counts(cloud):
    sync(cloud)
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist.remove("lucky")
    sync(cloud, now=NOW + timedelta(hours=1))
    cloud.videos["lucky-edit"] = FakeVideo("Get Lucky (Radio Edit)", "Columbia Records", "PT4M8S")
    playlist.append("lucky-edit")
    entries = cloud.spotify_playlists["road-trip"]["entries"]
    change_playlist(cloud, "road-trip", [*entries, HEY_JUDE.entry])
    # Enough to list the playlist and look up a video, not to add Hey Jude's.
    cloud.units_left = 1 + 1
    sync(cloud, now=NOW + timedelta(hours=2))
    assert "quota" in cloud.mappings[0]["last_error"]
    cloud.units_left = 10_000
    sync(cloud, now=NOW + timedelta(hours=3))
    assert cloud.cache[GET_LUCKY.isrc]["youtube_video_id"] == "lucky-edit"

    # It can still be replaced in turn.
    cloud.videos["lucky-live"] = FakeVideo("Get Lucky (Live)", "Daft Punk", "PT6M2S")
    playlist[playlist.index("lucky-edit")] = "lucky-live"
    assert sync(cloud, now=NOW + timedelta(hours=4)) == 0

    assert GET_LUCKY.track_id in cloud.spotify_track_ids("road-trip")


def test_a_better_match_replaces_the_copy_in_all_its_pairs(cloud):
    compilation = {"is_local": False, "item": GET_LUCKY.entry["item"] | {"id": "lucky-2"}}
    sync(cloud)
    entries = cloud.spotify_playlists["road-trip"]["entries"]
    change_playlist(cloud, "road-trip", [*entries, compilation])
    sync(cloud, now=NOW + timedelta(hours=1))
    # Known as Get Lucky from another playlist, but not by its title.
    cloud.videos["lucky-pw"] = FakeVideo("Get Lucky", "Pharrell Williams - Topic", "PT4M8S")
    cloud.video_cache["lucky-pw"] = {
        "youtube_video_id": "lucky-pw",
        "spotify_track_id": GET_LUCKY.track_id,
        "isrc": GET_LUCKY.isrc,
        "searched_at": NOW.isoformat(),
    }
    playlist = cloud.youtube_playlists["yt-road-trip"]
    playlist[playlist.index("lucky")] = "lucky-pw"
    cloud.requests.clear()

    assert sync(cloud, now=NOW + timedelta(hours=2)) == 0

    assert changes(cloud) == []
    assert {(GET_LUCKY.track_id, "lucky-pw"), ("lucky-2", "lucky-pw")} <= pairs(cloud)


def test_a_song_listed_twice_counts_as_added_the_first_time():
    old, new = (replace(DREAMS.track, added_at=at) for at in (LAST_WEEK, NOW))

    assert Playlists([new, old], []).added_at(Side.SPOTIFY, DREAMS.track_id) == LAST_WEEK

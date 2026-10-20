from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_cloud import SUPABASE_KEY, SUPABASE_URL, FakeCloud, spotify_entry

from cronchords.__main__ import run, spotify_access_token
from cronchords.spotify import SpotifyAuthError
from cronchords.supabase import AppConfig, Database

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

GET_LUCKY = spotify_entry("USQX91300108", "Get Lucky", "Daft Punk", 248_000)
RHAPSODY = spotify_entry("GBUM71029604", "Bohemian Rhapsody - Remastered 2011", "Queen", 354_000)
DREAMS = spotify_entry("USWB10002400", "Dreams", "Fleetwood Mac", 257_000)
HEY_JUDE = spotify_entry("GBAYE0601690", "Hey Jude - Remastered 2015", "The Beatles", 431_000)


def sync(cloud: FakeCloud, now: datetime = NOW) -> int:
    db = Database(SUPABASE_URL, SUPABASE_KEY, transport=cloud.transport)
    try:
        with httpx.Client(transport=cloud.transport) as http:
            return run(db, http, now=lambda: now)
    finally:
        db.close()


def searched(cloud: FakeCloud) -> list[str]:
    return [r.url.params["q"] for r in cloud.calls("www.googleapis.com", "/youtube/v3/search")]


def change_playlist(cloud: FakeCloud, playlist_id: str, *entries: dict) -> None:
    playlist = cloud.spotify_playlists[playlist_id]
    playlist["snapshot_id"] += "+"
    playlist["entries"].extend(entries)


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    cloud.spotify_playlists["road-trip"] = {
        "snapshot_id": "snap-1",
        "entries": [GET_LUCKY, RHAPSODY, DREAMS],
    }
    cloud.add_mapping("road-trip", "yt-road-trip")
    cloud.add_video("Daft Punk - Get Lucky", "lucky", "Get Lucky", "Daft Punk - Topic", "PT4M8S")
    cloud.add_video(
        "Queen - Bohemian Rhapsody - Remastered 2011",
        "rhapsody",
        "Bohemian Rhapsody (Remastered 2011)",
        "Queen - Topic",
        "PT5M54S",
    )
    cloud.add_video(
        "Fleetwood Mac - Dreams", "dreams", "Dreams", "Fleetwood Mac - Topic", "PT4M17S"
    )
    cloud.add_video(
        "The Beatles - Hey Jude - Remastered 2015",
        "jude",
        "Hey Jude (Remastered 2015)",
        "The Beatles - Topic",
        "PT7M11S",
    )
    return cloud


def test_first_sync_adds_every_track(cloud):
    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody", "dreams"]
    assert {isrc: entry["youtube_video_id"] for isrc, entry in cloud.cache.items()} == {
        "USQX91300108": "lucky",
        "GBUM71029604": "rhapsody",
        "USWB10002400": "dreams",
    }
    mapping = cloud.mappings[0]
    assert mapping["spotify_snapshot_id"] == "snap-1"
    assert mapping["last_synced_at"] == NOW.isoformat()
    assert mapping["last_error"] is None


def test_unchanged_playlist_costs_no_youtube_quota(cloud):
    sync(cloud)
    cloud.requests.clear()
    later = NOW + timedelta(hours=1)

    assert sync(cloud, now=later) == 0

    assert cloud.calls("www.googleapis.com") == []
    assert cloud.calls("api.spotify.com", "/v1/playlists/road-trip/items") == []
    assert cloud.mappings[0]["last_synced_at"] == later.isoformat()


def test_only_songs_missing_from_the_cache_are_searched(cloud):
    sync(cloud)
    change_playlist(cloud, "road-trip", HEY_JUDE)
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert searched(cloud) == ["The Beatles - Hey Jude - Remastered 2015"]
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody", "dreams", "jude"]


def test_cache_is_shared_between_playlists(cloud):
    sync(cloud)
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [GET_LUCKY]}
    cloud.add_mapping("favourites", "yt-favourites")
    cloud.requests.clear()

    assert sync(cloud) == 0

    assert searched(cloud) == []
    assert cloud.youtube_playlists["yt-favourites"] == ["lucky"]


def test_videos_already_in_the_playlist_are_not_added_again(cloud):
    cloud.youtube_playlists["yt-road-trip"] = ["rhapsody"]
    cloud.spotify_playlists["road-trip"]["entries"].append(GET_LUCKY)

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-road-trip"] == ["rhapsody", "lucky", "dreams"]


def test_tracks_that_cannot_be_matched_are_skipped(cloud):
    episode = spotify_entry(None, "Some Podcast", "Host")
    episode["item"]["type"] = "episode"
    local_file = {"is_local": True, "item": {"type": "track", "id": None, "name": "Demo"}}
    no_isrc = spotify_entry(None, "Unreleased", "Someone")
    cloud.spotify_playlists["road-trip"]["entries"] = [episode, GET_LUCKY, local_file, no_isrc]

    assert sync(cloud) == 0

    assert searched(cloud) == ["Daft Punk - Get Lucky"]
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]


def test_large_playlists_are_paged(cloud):
    entries = [
        spotify_entry(f"QZTEST{i:06d}", f"Song {i}", "Band", track_id=f"t{i}") for i in range(120)
    ]
    cloud.spotify_playlists["road-trip"]["entries"] = entries
    cloud.youtube_playlists["yt-road-trip"] = [f"v{i}" for i in range(120)]
    for i in range(120):
        cloud.cache[f"QZTEST{i:06d}"] = {
            "isrc": f"QZTEST{i:06d}",
            "spotify_track_id": f"t{i}",
            "youtube_video_id": f"v{i}",
            "searched_at": NOW.isoformat(),
        }

    assert sync(cloud) == 0

    assert len(cloud.calls("api.spotify.com", "/v1/playlists/road-trip/items")) == 3
    assert len(cloud.calls("www.googleapis.com", "/youtube/v3/playlistItems")) == 3
    assert searched(cloud) == []
    assert cloud.mappings[0]["spotify_snapshot_id"] == "snap-1"


def test_search_limit_pauses_the_sync_until_the_next_run(cloud):
    cloud.searches_left = 2

    assert sync(cloud) == 0

    mapping = cloud.mappings[0]
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody"]
    assert mapping["spotify_snapshot_id"] is None
    assert mapping["last_error"].startswith("1 song(s) still to sync")

    cloud.searches_left = 100
    cloud.requests.clear()
    assert sync(cloud) == 0

    assert searched(cloud) == ["Fleetwood Mac - Dreams"]
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody", "dreams"]
    assert mapping["spotify_snapshot_id"] == "snap-1"
    assert mapping["last_error"] is None


def test_cached_songs_are_still_added_after_the_search_limit_is_hit(cloud):
    sync(cloud)
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [HEY_JUDE, DREAMS]}
    cloud.add_mapping("favourites", "yt-favourites")
    cloud.searches_left = 0

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-favourites"] == ["dreams"]
    assert cloud.mappings[1]["spotify_snapshot_id"] is None


def test_running_out_of_quota_stops_the_run(cloud):
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [GET_LUCKY]}
    cloud.add_mapping("favourites", "yt-favourites")
    # Listing the playlist, then one lookup and one insert; the second insert fails.
    cloud.units_left = 1 + 1 + 50 + 1

    assert sync(cloud) == 0

    road_trip, favourites = cloud.mappings
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky"]
    assert road_trip["spotify_snapshot_id"] is None
    assert road_trip["last_error"] == "Waiting for the YouTube API quota to reset"
    assert cloud.calls("api.spotify.com", "/v1/playlists/favourites") == []
    assert favourites["last_error"] is None

    cloud.units_left = 10_000
    cloud.requests.clear()
    assert sync(cloud) == 0

    assert searched(cloud) == ["Fleetwood Mac - Dreams"]
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody", "dreams"]
    assert cloud.youtube_playlists["yt-favourites"] == ["lucky"]


def test_a_broken_mapping_does_not_stop_the_others(cloud):
    cloud.add_mapping("deleted-playlist", "yt-other")
    cloud.mappings.reverse()

    assert sync(cloud) == 1

    broken, road_trip = cloud.mappings
    assert "not found" in broken["last_error"]
    assert broken["spotify_snapshot_id"] is None
    assert road_trip["spotify_snapshot_id"] == "snap-1"
    assert cloud.youtube_playlists["yt-road-trip"] == ["lucky", "rhapsody", "dreams"]


def test_missing_youtube_playlist_is_reported(cloud):
    del cloud.youtube_playlists["yt-road-trip"]

    assert sync(cloud) == 1

    assert "playlist" in cloud.mappings[0]["last_error"].lower()


def test_unmatched_songs_are_searched_again_after_a_while(cloud):
    cloud.search_results.pop("Fleetwood Mac - Dreams")
    sync(cloud)
    assert cloud.cache["USWB10002400"]["youtube_video_id"] is None
    assert cloud.mappings[0]["spotify_snapshot_id"] == "snap-1"

    change_playlist(cloud, "road-trip", HEY_JUDE)
    cloud.requests.clear()
    sync(cloud, now=NOW + timedelta(days=1))
    assert searched(cloud) == ["The Beatles - Hey Jude - Remastered 2015"]

    cloud.search_results["Fleetwood Mac - Dreams"] = ["dreams"]
    change_playlist(cloud, "road-trip")
    cloud.requests.clear()
    sync(cloud, now=NOW + timedelta(days=31))
    assert searched(cloud) == ["Fleetwood Mac - Dreams"]
    assert cloud.youtube_playlists["yt-road-trip"][-1] == "dreams"


def test_removed_videos_are_forgotten(cloud):
    sync(cloud)
    cloud.videos["dreams"].removed = True
    cloud.spotify_playlists["favourites"] = {"snapshot_id": "fav-1", "entries": [DREAMS]}
    cloud.add_mapping("favourites", "yt-favourites")

    assert sync(cloud) == 0

    assert cloud.youtube_playlists["yt-favourites"] == []
    assert cloud.cache["USWB10002400"]["youtube_video_id"] is None
    assert cloud.mappings[1]["spotify_snapshot_id"] == "fav-1"


def test_rotated_spotify_refresh_token_is_saved(cloud):
    sync(cloud)
    assert cloud.config["spotify_refresh_token"] == "spotify-refresh-2"

    sync(cloud, now=NOW + timedelta(hours=1))
    assert cloud.config["spotify_refresh_token"] == "spotify-refresh-3"


def test_token_rotated_by_the_dashboard_meanwhile_is_picked_up(cloud):
    cloud.spotify_rotations = 2
    cloud.config["spotify_refresh_token"] = "spotify-refresh-2"
    stale = AppConfig(spotify_client_id="spotify-client", spotify_refresh_token="spotify-refresh-1")
    db = Database(SUPABASE_URL, SUPABASE_KEY, transport=cloud.transport)

    with httpx.Client(transport=cloud.transport) as http:
        assert spotify_access_token(db, http, stale) == "spotify-access"
    db.close()

    assert cloud.config["spotify_refresh_token"] == "spotify-refresh-3"


def test_expired_spotify_authorization_stops_everything(cloud):
    cloud.spotify_rotations = 7

    with pytest.raises(SpotifyAuthError, match="Reconnect Spotify"):
        sync(cloud)

    assert cloud.calls("www.googleapis.com") == []


def test_nothing_happens_without_mappings():
    cloud = FakeCloud()

    assert sync(cloud) == 0

    assert cloud.calls("accounts.spotify.com") == []


def test_unfinished_setup_fails_the_run(cloud):
    cloud.config["youtube_refresh_token"] = None

    assert sync(cloud) == 1

    assert cloud.calls("accounts.spotify.com") == []


def test_warns_before_spotify_access_runs_out(cloud, caplog):
    cloud.config["spotify_connected_at"] = (NOW - timedelta(days=170)).isoformat()

    sync(cloud)

    assert "Reconnect Spotify" in caplog.text

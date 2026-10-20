from datetime import UTC, datetime

import httpx
import pytest

from cronchords.supabase import CacheEntry, Database, DatabaseError, Direction


def database(handler, key: str = "sb_secret_abc") -> Database:
    return Database("https://project.supabase.co/", key, transport=httpx.MockTransport(handler))


def test_new_secret_keys_are_only_sent_as_apikey():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    database(handler).get_mappings()

    assert seen[0].headers["apikey"] == "sb_secret_abc"
    assert "Authorization" not in seen[0].headers
    assert seen[0].url.path == "/rest/v1/playlist_mappings"


def test_legacy_service_role_keys_are_also_sent_as_bearer_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    database(handler, key="eyJhbGciOiJIUzI1NiJ9.legacy").get_mappings()

    assert seen[0].headers["Authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.legacy"


def test_cache_lookups_are_split_into_chunks():
    filters = []

    def handler(request: httpx.Request) -> httpx.Response:
        filters.append(request.url.params["isrc"])
        return httpx.Response(200, json=[])

    database(handler).get_cache_entries(f"ISRC{i:08d}" for i in range(250))

    assert len(filters) == 3
    assert filters[0].startswith("in.(ISRC00000000,ISRC00000001,")


def test_errors_carry_the_postgrest_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "permission denied for table app_config"})

    with pytest.raises(DatabaseError, match="permission denied for table app_config"):
        database(handler).get_config()


def test_missing_config_row_points_at_the_likely_cause():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with pytest.raises(DatabaseError, match="secret key"):
        database(handler).get_config()


def test_mappings_know_their_direction_and_synced_songs():
    def handler(request: httpx.Request) -> httpx.Response:
        row = {
            "id": "m1",
            "spotify_playlist_id": "sp",
            "youtube_playlist_id": "yt",
            "direction": "both",
            "spotify_snapshot_id": None,
            "youtube_snapshot_id": None,
            "synced_songs": [["track", "video"]],
            "spotify_playlist_name": "Road Trip",
        }
        return httpx.Response(200, json=[row])

    [mapping] = database(handler).get_mappings()

    assert mapping.direction is Direction.BOTH
    assert mapping.to_spotify and mapping.to_youtube
    assert mapping.synced_songs == frozenset({("track", "video")})


def test_videos_can_be_looked_up_by_the_tracks_they_were_matched_to():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["youtube_video_id"])
        row = {
            "isrc": "GBUM71029604",
            "spotify_track_id": "t1",
            "youtube_video_id": "v1",
            "searched_at": "2026-10-20T12:00:00+00:00",
        }
        return httpx.Response(
            200, json=[row, row | {"isrc": "GBUM70000000", "spotify_track_id": "t2"}]
        )

    entries = database(handler).get_cache_entries_for_videos(["v1", "v2"])

    assert seen == ["in.(v1,v2)"]
    assert [entry.spotify_track_id for entry in entries["v1"]] == ["t1", "t2"]


def test_cache_entries_can_be_added_without_overwriting():
    prefer = []

    def handler(request: httpx.Request) -> httpx.Response:
        prefer.append(request.headers["Prefer"])
        return httpx.Response(201)

    entry = CacheEntry("GBUM71029604", "t1", "v1", datetime(2026, 10, 20, tzinfo=UTC))
    db = database(handler)
    db.save_cache_entry(entry)
    db.save_cache_entry(entry, overwrite=False)

    assert prefer == [
        "resolution=merge-duplicates,return=minimal",
        "resolution=ignore-duplicates,return=minimal",
    ]

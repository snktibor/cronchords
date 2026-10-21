import json
from datetime import UTC, datetime

import httpx
import pytest

from cronchords.spotify import (
    PlaylistChangedError,
    SpotifyClient,
    SpotifyError,
    SpotifyRateLimitError,
    Track,
)


def client(handler) -> SpotifyClient:
    return SpotifyClient(httpx.Client(transport=httpx.MockTransport(handler)), "token")


def test_search_results_keep_tracks_without_an_isrc():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "track"
        assert request.url.params["limit"] == "10"
        track = {"type": "track", "artists": [{"name": "Fleetwood Mac"}]}
        return httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [
                        track
                        | {"id": "a", "name": "Dreams", "duration_ms": 257_000}
                        | {"external_ids": {"isrc": "us-wb1-00-02400"}},
                        track | {"id": "b", "name": "Dreams (Demo)", "duration_ms": 250_000},
                        None,
                    ]
                }
            },
        )

    spotify = client(handler)
    dreams, demo = spotify.search("Dreams Fleetwood Mac")

    assert dreams == Track("a", "USWB10002400", "Dreams", ("Fleetwood Mac",), 257_000)
    assert demo.isrc is None
    assert spotify.searches == 1


def test_tracks_are_added_and_removed_in_batches_of_100():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.method, json.loads(request.content)))
        return httpx.Response(201 if request.method == "POST" else 200, json={"snapshot_id": "s"})

    spotify = client(handler)
    track_ids = [f"t{i}" for i in range(150)]
    spotify.add_tracks("PL1", track_ids)
    spotify.remove_tracks("PL1", track_ids[:2])

    (_, first), (_, second), (method, removal) = bodies
    assert len(first["uris"]) == 100
    assert second["uris"][-1] == "spotify:track:t149"
    assert method == "DELETE"
    assert removal == {"items": [{"uri": "spotify:track:t0"}, {"uri": "spotify:track:t1"}]}


def test_missing_permission_asks_to_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"status": 403, "message": "Insufficient client scope"}}
        )

    with pytest.raises(SpotifyError, match=r"Insufficient client scope.*Reconnect Spotify"):
        client(handler).add_tracks("PL1", ["t1"])


def test_long_rate_limits_are_not_waited_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "86400"})

    with pytest.raises(SpotifyRateLimitError):
        client(handler).search("anything")


@pytest.mark.parametrize(("total", "snapshot_after"), [(2, "s1"), (1, "s2")])
def test_a_playlist_that_changed_while_being_read_is_not_used(total, snapshot_after):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items"):
            item = {"type": "track", "id": "a", "name": "Dreams", "artists": []}
            entries = [{"is_local": False, "item": item}]
            return httpx.Response(200, json={"total": total, "next": None, "items": entries})
        return httpx.Response(200, json={"snapshot_id": snapshot_after})

    with pytest.raises(PlaylistChangedError):
        client(handler).get_tracks("PL1", "s1")


def test_playlist_tracks_know_when_they_were_added():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items"):
            assert "added_at" in request.url.params["fields"]
            item = {"type": "track", "id": "a", "name": "Dreams", "artists": []}
            entries = [
                {"added_at": "2026-10-20T12:00:00Z", "is_local": False, "item": item},
                # Very old playlists don't know.
                {"added_at": None, "is_local": False, "item": item | {"id": "b"}},
            ]
            return httpx.Response(200, json={"total": 2, "next": None, "items": entries})
        return httpx.Response(200, json={"snapshot_id": "s1"})

    (new, old), _ = client(handler).get_tracks("PL1", "s1")

    assert new.added_at == datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert old.added_at is None

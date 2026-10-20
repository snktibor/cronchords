import httpx
import pytest

from cronchords.youtube import (
    QuotaExceededError,
    SearchQuotaExceededError,
    YouTubeAuthError,
    YouTubeClient,
    parse_duration,
    refresh_access_token,
)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT4M13S", 253),
        ("PT1H2M3S", 3723),
        ("PT45S", 45),
        ("PT3M", 180),
        ("P1DT1S", 86401),
        ("P0D", 0),
        ("PT", None),
        ("", None),
        ("4:13", None),
    ],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def client(handler) -> YouTubeClient:
    return YouTubeClient(httpx.Client(transport=httpx.MockTransport(handler)), "token")


def quota_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        403, json={"error": {"message": "Quota exceeded", "errors": [{"reason": "quotaExceeded"}]}}
    )


def test_search_quota_is_told_apart_from_the_shared_quota():
    youtube = client(quota_error)

    with pytest.raises(SearchQuotaExceededError):
        youtube.search("anything")
    with pytest.raises(QuotaExceededError):
        youtube.playlist_video_ids("PL1")


def test_search_results_are_unescaped():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "video"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": {"videoId": "abc"},
                        "snippet": {
                            "title": "Sweet Child O&#39; Mine",
                            "channelTitle": "Guns N&#39; Roses - Topic",
                        },
                    }
                ]
            },
        )

    [result] = client(handler).search("Guns N' Roses - Sweet Child O' Mine")

    assert result.title == "Sweet Child O' Mine"
    assert result.channel == "Guns N' Roses - Topic"


def test_quota_usage_is_counted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    youtube = client(handler)
    youtube.search("a")
    youtube.durations(["x", "y"])
    youtube.add_to_playlist("PL1", "x")

    assert youtube.searches == 1
    assert youtube.units_used == 51


def test_revoked_refresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        pytest.raises(YouTubeAuthError, match="Reconnect YouTube"),
    ):
        refresh_access_token(http, "id", "secret", "refresh")

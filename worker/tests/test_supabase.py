import httpx
import pytest

from cronchords.supabase import Database, DatabaseError


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

"""Run one sync pass: python -m cronchords"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from . import spotify, youtube
from .supabase import AppConfig, Database, DatabaseError
from .sync import Syncer

log = logging.getLogger("cronchords")

# Spotify refresh tokens expire six months after the user connected the app.
SPOTIFY_TOKEN_LIFETIME = timedelta(days=180)
SPOTIFY_EXPIRY_WARNING = timedelta(days=14)

IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def main() -> int:
    setup_logging()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        log.error("SUPABASE_URL and SUPABASE_SECRET_KEY have to be set")
        return 1

    db = Database(url, key)
    try:
        with httpx.Client(timeout=30) as http:
            return run(db, http)
    except (DatabaseError, spotify.SpotifyError, youtube.YouTubeError) as exc:
        log.error("%s", exc)
        return 1
    finally:
        db.close()


def run(
    db: Database,
    http: httpx.Client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    config = db.get_config()
    mappings = db.get_mappings()
    if not mappings:
        log.info("No playlists to sync yet, add one in the dashboard")
        return 0

    missing = config.missing()
    if missing:
        log.error(
            "Not set up yet, missing: %s. Finish the setup in the dashboard.", ", ".join(missing)
        )
        return 1
    mask(config.spotify_refresh_token, config.google_client_secret, config.youtube_refresh_token)

    spotify_token = spotify_access_token(db, http, config)
    youtube_token = youtube.refresh_access_token(
        http, config.google_client_id, config.google_client_secret, config.youtube_refresh_token
    )
    mask(spotify_token, youtube_token)
    warn_if_spotify_expires_soon(config, now())

    sp = spotify.SpotifyClient(http, spotify_token)
    yt = youtube.YouTubeClient(http, youtube_token)
    result = Syncer(db, sp, yt, now=now).run(mappings)

    log.info(
        "Done: %d of %d playlist(s) synced, %d failed. YouTube quota used: ~%d units, "
        "%d searches. Spotify searches: %d.",
        result.synced,
        len(mappings),
        result.failed,
        yt.units_used,
        yt.searches,
        sp.searches,
    )
    return 1 if result.failed else 0


def spotify_access_token(db: Database, http: httpx.Client, config: AppConfig) -> str:
    """Get a Spotify access token and store the rotated refresh token."""
    try:
        grant = spotify.refresh_access_token(
            http, config.spotify_client_id, config.spotify_refresh_token
        )
    except spotify.SpotifyAuthError:
        # The dashboard may have rotated the token since this run read it.
        latest = db.get_config()
        if latest.spotify_refresh_token in (None, config.spotify_refresh_token):
            raise
        config = latest
        mask(config.spotify_refresh_token)
        grant = spotify.refresh_access_token(
            http, config.spotify_client_id, config.spotify_refresh_token
        )

    if grant.refresh_token and grant.refresh_token != config.spotify_refresh_token:
        mask(grant.refresh_token)
        db.update_config(spotify_refresh_token=grant.refresh_token)
    return grant.access_token


def warn_if_spotify_expires_soon(config: AppConfig, now: datetime) -> None:
    if not config.spotify_connected_at:
        return
    expires = config.spotify_connected_at + SPOTIFY_TOKEN_LIFETIME
    if expires - now <= SPOTIFY_EXPIRY_WARNING:
        log.warning(
            "Spotify access runs out around %s. Reconnect Spotify in the dashboard to keep "
            "syncing.",
            expires.date().isoformat(),
        )


def mask(*secrets: str | None) -> None:
    """Hide values from the GitHub Actions log (logs of public repos are public)."""
    if not IN_GITHUB_ACTIONS:
        return
    for secret in secrets:
        if secret:
            print(f"::add-mask::{secret}", flush=True)


class ActionsFormatter(logging.Formatter):
    """Shows warnings and errors as annotations on the GitHub Actions run."""

    PREFIXES = {logging.WARNING: "::warning::", logging.ERROR: "::error::"}

    def format(self, record: logging.LogRecord) -> str:
        return self.PREFIXES.get(record.levelno, "") + super().format(record)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if IN_GITHUB_ACTIONS:
        handler.setFormatter(ActionsFormatter("%(message)s"))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
        )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # httpx logs every request URL at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)


if __name__ == "__main__":
    sys.exit(main())

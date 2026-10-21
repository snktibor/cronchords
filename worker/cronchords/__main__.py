"""Sync the playlists: python -m cronchords [--every MINUTES]

Without --every it syncs once and exits, the way the GitHub Actions workflow
runs it. With it, it keeps running and syncs every so many minutes, for a
machine of your own such as a Raspberry Pi.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import FrameType

import httpx

from . import spotify, youtube
from .supabase import AppConfig, Database, DatabaseError
from .sync import Syncer

log = logging.getLogger("cronchords")

# Spotify refresh tokens expire six months after the user connected the app.
SPOTIFY_TOKEN_LIFETIME = timedelta(days=180)
SPOTIFY_EXPIRY_WARNING = timedelta(days=14)

IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        log.error("SUPABASE_URL and SUPABASE_SECRET_KEY have to be set")
        return 1
    if args.every is None:
        return sync_once(url, key)

    # Ctrl+C stops it too, without waiting for the sync to finish.
    with contextlib.suppress(KeyboardInterrupt):
        keep_syncing(lambda: sync_once(url, key), args.every)
    log.info("Stopped")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cronchords",
        description="Sync the playlist pairs set up in the dashboard.",
    )
    parser.add_argument(
        "--every",
        type=minutes,
        metavar="MINUTES",
        help="keep running and sync every MINUTES minutes, starting right away",
    )
    return parser.parse_args(argv)


def minutes(value: str) -> timedelta:
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError(
            f"expected a whole number of minutes (1 or more), got {value!r}"
        )
    return timedelta(minutes=int(value))


def keep_syncing(
    sync: Callable[[], object],
    every: timedelta,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    """Sync right away and then every so often, until SIGTERM arrives.

    Syncs start `every` apart, unless one takes longer than that. SIGTERM is
    what docker stop and systemctl stop send; a sync that has started is
    finished first.
    """
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            next_sync = clock() + every.total_seconds()
            try:
                sync()
            except Exception:
                log.exception("The sync stopped with an unexpected error")
            if not stopping:
                at = datetime.now() + timedelta(seconds=max(next_sync - clock(), 0))
                log.info("Next sync at %s", f"{at:%H:%M}")
            # Short naps, so that SIGTERM doesn't have to wait for the next sync.
            while not stopping and clock() < next_sync:
                sleep(min(next_sync - clock(), 1))
    finally:
        signal.signal(signal.SIGTERM, previous)


def sync_once(url: str, key: str) -> int:
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
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # httpx logs every request URL at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)


if __name__ == "__main__":
    sys.exit(main())

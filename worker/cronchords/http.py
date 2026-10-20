from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Spotify sometimes answers 429 with a Retry-After of several hours. Waiting
# that long inside a cron job makes no sense, the next run will try again.
MAX_RETRY_AFTER = 120


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying network errors and transient HTTP errors."""
    attempt = 1
    while True:
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt >= attempts:
                raise
            delay = 2.0**attempt
            log.info("%s %s failed (%s), retrying in %.0fs", method, _path(url), exc, delay)
        else:
            if response.status_code not in retry_statuses or attempt >= attempts:
                return response
            delay = _retry_after(response) or 2.0**attempt
            if delay > MAX_RETRY_AFTER:
                return response
            log.info(
                "%s %s returned %d, retrying in %.0fs",
                method,
                _path(url),
                response.status_code,
                delay,
            )
        time.sleep(delay)
        attempt += 1


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(float(response.headers.get("Retry-After", "")), 0.0)
    except ValueError:
        return None


def _path(url: str) -> str:
    return httpx.URL(url).path or url

"""HTTP helpers for Consurf API calls with rate-limit retry."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

DEFAULT_MAX_RETRIES = 60
DEFAULT_INITIAL_WAIT = 5.0
DEFAULT_MAX_WAIT = 120.0


def is_consurf_rate_limited(response: requests.Response | None) -> bool:
    return response is not None and response.status_code == 429


def consurf_get(
    url: str,
    *,
    timeout: float = 10,
    max_retries: int = DEFAULT_MAX_RETRIES,
    session: requests.Session | None = None,
    logger: logging.Logger | None = None,
) -> requests.Response | None:
    """GET a Consurf URL, waiting and retrying on HTTP 429.

    Returns the final response (including 404/400), or ``None`` after repeated
    network failures. Keeps retrying 429 responses until a definitive status is
    received or ``max_retries`` is exhausted.
    """
    log = logger or logging.getLogger(__name__)
    http = session or requests
    wait = DEFAULT_INITIAL_WAIT

    for attempt in range(1, max_retries + 1):
        try:
            response = http.get(url, timeout=timeout)
        except requests.RequestException as exc:
            log.warning(
                "Consurf request failed for %s (attempt %d/%d): %s",
                url,
                attempt,
                max_retries,
                exc,
            )
            if attempt >= max_retries:
                return None
            time.sleep(min(wait, DEFAULT_MAX_WAIT))
            wait = min(wait * 2, DEFAULT_MAX_WAIT)
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            sleep_s = DEFAULT_INITIAL_WAIT
            if retry_after:
                try:
                    sleep_s = max(float(retry_after), DEFAULT_INITIAL_WAIT)
                except (TypeError, ValueError):
                    sleep_s = min(wait, DEFAULT_MAX_WAIT)
            else:
                sleep_s = min(wait, DEFAULT_MAX_WAIT)
            log.warning(
                "Consurf rate limited for %s; waiting %.0fs (attempt %d/%d)",
                url,
                sleep_s,
                attempt,
                max_retries,
            )
            time.sleep(sleep_s)
            wait = min(wait * 2, DEFAULT_MAX_WAIT)
            continue

        return response

    log.error("Consurf rate limit persisted for %s after %d attempts", url, max_retries)
    return response if "response" in locals() else None

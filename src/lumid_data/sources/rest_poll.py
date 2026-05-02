"""REST-poll pull connector.

Hits an HTTP endpoint on demand, returns the (status, body, headers)
tuple. Caller decides what to do with the body (typically pipe it
through the ingest pipeline). Cursor / pagination is up to the caller —
pass query params via ``options``.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


@dataclass(frozen=True)
class RestPollSpec:
    url: str
    method: str = "GET"
    headers: dict[str, str] | None = None
    options: dict[str, Any] | None = None  # passed as query params


def fetch(spec: RestPollSpec) -> tuple[int, bytes, dict[str, str]]:
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.request(
            spec.method.upper(),
            spec.url,
            headers=spec.headers or {},
            params=spec.options or {},
        )
    return r.status_code, r.content, dict(r.headers)

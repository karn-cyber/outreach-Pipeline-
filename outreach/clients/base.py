"""Shared HTTP plumbing for every integration.

Each stage's client subclasses `BaseClient` so retries, rate limiting and error
handling are written once and behave identically across all four APIs. Keeping
this in one place is what lets each stage module stay small and only describe
*its own* endpoint shape.

Design choices worth explaining in the interview:

* Retries are done by hand (not via a library) so we can honour the server's
  `Retry-After` header on 429s instead of guessing.
* We only retry transient failures (429, 5xx, network errors). A 400/401/404 is
  a bug or a bad key — retrying just wastes time and credits, so we fail fast.
* A minimum interval between requests gives us simple, predictable throttling.
"""
from __future__ import annotations

import random
import time
from typing import Any, Optional

import httpx

from ..logging_conf import get_logger

log = get_logger(__name__)


# --- typed errors so callers can react precisely ----------------------------

class ApiError(Exception):
    """Non-retryable API error (bad request, unexpected shape, etc.)."""

    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(ApiError):
    """401/403 — bad or missing credentials. Never retried."""


class NotFoundError(ApiError):
    """404 — resource/contact not found. Handled per-item, not fatal."""


class RateLimitError(ApiError):
    """429 — retryable after a delay."""


class RetryableServerError(ApiError):
    """5xx / network blip — retryable."""


class BaseClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        *,
        name: str,
        min_interval: float = 0.25,
        max_retries: int = 4,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_call = 0.0
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout
        )

    # context-manager support so callers can `with OceanClient(...) as c:`
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
    ) -> Any:
        """Make one request with throttling, retries and error mapping.

        Returns the parsed JSON body on success.
        """
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                resp = self._client.request(method, path, json=json, params=params)
            except httpx.TransportError as exc:
                # connection reset, DNS, timeout, etc. -> retry
                if attempt > self.max_retries:
                    raise RetryableServerError(
                        f"{self.name}: network error after {attempt} attempts: {exc}"
                    ) from exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code < 300:
                return self._parse(resp)

            self._handle_error_status(resp, attempt)
            # _handle_error_status either raises or returns to signal a retry
            self._sleep_backoff(attempt, resp)

    # -- helpers --

    def _parse(self, resp: httpx.Response) -> Any:
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ApiError(
                f"{self.name}: expected JSON, got {resp.text[:200]!r}",
                status=resp.status_code,
            ) from exc

    def _handle_error_status(self, resp: httpx.Response, attempt: int) -> None:
        status = resp.status_code
        body = resp.text[:500]
        if status in (401, 403):
            raise AuthError(f"{self.name}: authentication failed ({status}).", status, body)
        if status == 404:
            raise NotFoundError(f"{self.name}: not found (404).", status, body)
        if status == 429:
            if attempt > self.max_retries:
                raise RateLimitError(
                    f"{self.name}: still rate limited after {attempt} attempts.", status, body
                )
            log.warning("%s: rate limited (429), backing off (attempt %d).", self.name, attempt)
            return  # signal retry
        if status >= 500:
            if attempt > self.max_retries:
                raise RetryableServerError(
                    f"{self.name}: server error {status} after {attempt} attempts.", status, body
                )
            log.warning("%s: server error %d, retrying (attempt %d).", self.name, status, attempt)
            return  # signal retry
        # everything else (400, 422, ...) is a real client error -> fail fast
        raise ApiError(f"{self.name}: request failed ({status}): {body}", status, body)

    def _sleep_backoff(self, attempt: int, resp: Optional[httpx.Response] = None) -> None:
        # honour Retry-After if the server told us how long to wait
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(int(retry_after))
                return
        # otherwise exponential backoff with jitter: ~0.5, 1, 2, 4s
        delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
        time.sleep(delay + random.uniform(0, 0.4))

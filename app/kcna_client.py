"""HTTP client for kcna.kp.

The origin is slow and unreliable and serves only a shell to unknown agents, so
this client: (1) presents a browser User-Agent, (2) warms up a cookie session by
visiting the homepage, (3) retries with exponential backoff, and (4) rate-limits
itself between requests.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import config

log = logging.getLogger("kcna.client")

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException)


class KcnaClient:
    def __init__(self, base_url: str = config.BASE_URL, timeout: float | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout or config.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "ko,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        self._warmed = False
        self._last_request = 0.0

    # -- internals -------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < config.REQUEST_DELAY:
            time.sleep(config.REQUEST_DELAY - elapsed)
        self._last_request = time.monotonic()

    def warmup(self, lang: str = "kp") -> None:
        """Establish a session cookie by hitting the homepage once."""
        if self._warmed:
            return
        try:
            self._throttle()
            r = self._client.get(f"/{lang}")
            r.raise_for_status()
            self._warmed = True
            log.info("session warmed up (cookies=%d)", len(self._client.cookies))
        except Exception as e:  # warmup is best-effort
            log.warning("warmup failed (continuing anyway): %s", e)
            self._warmed = True

    # -- public ----------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        reraise=True,
    )
    def get_text(self, path: str, *, referer: Optional[str] = None) -> str:
        self.warmup()
        self._throttle()
        headers = {"Referer": referer} if referer else {}
        r = self._client.get(path, headers=headers)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        return r.text

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        reraise=True,
    )
    def get_bytes(self, path: str, *, referer: Optional[str] = None) -> tuple[bytes, str]:
        self.warmup()
        self._throttle()
        headers = {"Referer": referer} if referer else {}
        r = self._client.get(path, headers=headers)
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "")

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        reraise=True,
    )
    def post_text(self, path: str, data: dict, *, referer: Optional[str] = None) -> str:
        """Form POST (used by Rodong's photo-gallery API). Sets the AJAX header
        the site's own jQuery calls use."""
        self.warmup()
        self._throttle()
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if referer:
            headers["Referer"] = referer
        r = self._client.post(path, data=data, headers=headers)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        return r.text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KcnaClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

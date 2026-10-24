"""Fetching, politely.

Two things here are about being allowed to keep scraping rather than about
getting data today: robots.txt is checked before the first request, and a delay
is held between requests to the same host. Most scrapers that die permanently
die because nobody did either.
"""

from __future__ import annotations

import time
import urllib.robotparser
from abc import ABC, abstractmethod
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..metrics import FETCH_ERRORS, PAGES_FETCHED, status_class

log = structlog.get_logger()


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids the URL we were about to request."""


class Fetcher:
    """HTTP client with retries, rate limiting and robots.txt enforcement."""

    def __init__(self, source: str, client: httpx.Client | None = None) -> None:
        self.source = source
        self._client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout,
            follow_redirects=True,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at: float = 0.0

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._robots:
            return self._robots[root]
        parser = urllib.robotparser.RobotFileParser()
        try:
            resp = self._client.get(urljoin(root, "/robots.txt"))
            parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            # No robots.txt reachable: treat as unrestricted, which is the
            # standard reading, but log it so the decision is visible.
            log.warning("robots_unreachable", source=self.source, root=root)
            parser.parse([])
        self._robots[root] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not settings.respect_robots:
            return True
        parser = self._robots_for(url)
        return parser.can_fetch(settings.user_agent, url) if parser else True

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = settings.rate_limit_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    def _request(self, url: str) -> httpx.Response:
        self._throttle()
        return self._client.get(url)

    def get(self, url: str) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        try:
            resp = self._request(url)
        except httpx.TimeoutException:
            FETCH_ERRORS.labels(self.source, "timeout").inc()
            raise
        except httpx.ConnectError:
            FETCH_ERRORS.labels(self.source, "connect").inc()
            raise
        PAGES_FETCHED.labels(self.source, status_class(resp.status_code)).inc()
        resp.raise_for_status()
        return resp

    def close(self) -> None:
        self._client.close()


class Spider(ABC):
    """One source. Implement `urls` and `parse` and the pipeline does the rest."""

    name: str
    nullable_fields: set[str] = set()

    @abstractmethod
    def urls(self) -> Iterator[str]:
        """Yield the pages this run should fetch."""

    @abstractmethod
    def parse(self, url: str, html: str) -> Iterator[dict]:
        """Yield raw dicts. Validation happens in the pipeline, not here.

        Return raw values rather than cleaned ones. A field that has gone
        missing should arrive as None or an empty string so the null-rate
        monitor can see it, instead of being silently defaulted.
        """

    def fetch(self, fetcher: Fetcher, url: str) -> str:
        return fetcher.get(url).text

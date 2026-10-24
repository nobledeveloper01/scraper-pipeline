"""An authenticated JSON source.

Two things worth noting beyond the login itself.

This parses JSON rather than HTML. Where a source offers an API, that is the
one to use: it is faster, the payload is stable across redesigns, and no
selector can move under you. The monitoring applies unchanged, because the
checks are on the shape of the output rather than on how it was obtained.

Credentials come from the environment, never from this file. A spider is
committed; a password must not be. Point `LISTINGS_BASE_URL` at a local
instance and set `LISTINGS_USERNAME` and `LISTINGS_PASSWORD` to run it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

from ..auth import AuthStrategy, BearerTokenAuth, NoAuth
from .base import Spider

DEFAULT_BASE = "http://127.0.0.1:3000"


class ListingsSpider(Spider):
    name = "listings"
    nullable_fields = {"bedrooms"}

    def __init__(self, pages: int = 1, base_url: str | None = None) -> None:
        self.pages = pages
        self.base_url = (base_url or os.getenv("LISTINGS_BASE_URL") or DEFAULT_BASE).rstrip("/")

    def auth(self) -> AuthStrategy:
        """Authenticate only when credentials are present.

        Falling back to NoAuth keeps the spider importable and testable in CI,
        where the credentials are deliberately absent, rather than exploding at
        construction time.
        """
        if not os.getenv("LISTINGS_USERNAME"):
            return NoAuth()
        return BearerTokenAuth.from_env(
            "LISTINGS",
            login_url=f"{self.base_url}/auth/login",
            refresh_url=f"{self.base_url}/auth/refresh",
            session_file=os.getenv("LISTINGS_SESSION_FILE", ".sessions/listings.json"),
        )

    def urls(self) -> Iterator[str]:
        for page in range(1, self.pages + 1):
            yield f"{self.base_url}/listings?page={page}"

    def parse(self, url: str, body: str) -> Iterator[dict]:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            # An HTML body on a JSON endpoint almost always means a login page
            # or an error page. Yield nothing and let the monitor call it,
            # rather than guessing at a recovery here.
            return

        records = payload.get("data") if isinstance(payload, dict) else payload
        for record in records or []:
            yield {
                "source": self.name,
                "source_url": url,
                "reference": record.get("reference"),
                "title": record.get("title"),
                "price": record.get("price"),
                "currency": record.get("currency"),
                "bedrooms": record.get("bedrooms"),
                "listing_type": record.get("listing_type"),
            }

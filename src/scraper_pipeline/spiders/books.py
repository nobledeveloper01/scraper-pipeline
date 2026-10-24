"""Static, server-rendered source.

Selector choices here are deliberate. Each field is anchored to a class name
that carries meaning (`product_pod`, `price_color`, `star-rating`) rather than
to a positional path like `div > div:nth-child(3) > p`. Positional selectors
work on the day you write them and break the first time someone adds a wrapper
div, which is the single most common cause of a scraper quietly dying.

When a field cannot be found this returns None rather than skipping the item or
substituting a default. That is what lets `drift.py` see the null rate climb and
tell you which selector moved.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import Spider

BASE = "https://books.toscrape.com/"
CATALOGUE = urljoin(BASE, "catalogue/page-{page}.html")

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


class BooksSpider(Spider):
    name = "books"
    # Rating is genuinely optional on this source, so it is excluded from the
    # null-rate alarm. Everything else missing means something broke.
    nullable_fields = {"rating"}

    def __init__(self, pages: int = 2) -> None:
        self.pages = pages

    def urls(self) -> Iterator[str]:
        for page in range(1, self.pages + 1):
            yield CATALOGUE.format(page=page)

    def parse(self, url: str, html: str) -> Iterator[dict]:
        soup = BeautifulSoup(html, "lxml")
        for pod in soup.select("article.product_pod"):
            yield {
                "source": self.name,
                "source_url": url,
                "title": self._title(pod),
                "price": self._price(pod),
                "currency": "GBP",
                "availability": self._availability(pod),
                "rating": self._rating(pod),
            }

    @staticmethod
    def _title(pod) -> str | None:
        # The anchor's title attribute holds the untruncated name; the link text
        # is ellipsised. Prefer the attribute and fall back to the text.
        link = pod.select_one("h3 > a")
        if link is None:
            return None
        return link.get("title") or link.get_text(strip=True) or None

    @staticmethod
    def _price(pod) -> str | None:
        el = pod.select_one("p.price_color")
        if el is None:
            return None
        raw = el.get_text(strip=True)
        cleaned = raw.lstrip("£$€").strip()
        return cleaned or None

    @staticmethod
    def _availability(pod) -> str | None:
        el = pod.select_one("p.instock.availability")
        if el is None:
            return None
        return el.get_text(strip=True) or None

    @staticmethod
    def _rating(pod) -> int | None:
        el = pod.select_one("p.star-rating")
        if el is None:
            return None
        for cls in el.get("class", []):
            if cls in RATING_WORDS:
                return RATING_WORDS[cls]
        return None

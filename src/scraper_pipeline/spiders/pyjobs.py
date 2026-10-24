"""A real site, with real markup.

The other two sources are published for scraping practice and their HTML is
tidy. This one is a production page and behaves like one: the company name is a
bare text node sitting after an anchor and a `<br/>`, with a stray tab in it,
rather than being wrapped in anything addressable. Job types are a list of
anchors. Whitespace is inconsistent.

Extracting the company is the interesting part. There is no element to select,
so the only thing available is the structure around it: take the parent span,
walk its direct children, and keep the text nodes that come after the title
anchor. It is uglier than a CSS selector and it is what the page actually
requires.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString

from .base import Spider

BASE = "https://www.python.org"
LISTING = urljoin(BASE, "/jobs/")


class PyJobsSpider(Spider):
    name = "pyjobs"
    # Not every posting carries a location or a type, and that is normal here
    # rather than a sign of breakage.
    nullable_fields = {"location", "job_types"}

    def __init__(self, pages: int = 1) -> None:
        self.pages = pages

    def urls(self) -> Iterator[str]:
        for page in range(1, self.pages + 1):
            yield LISTING if page == 1 else f"{LISTING}?page={page}"

    def parse(self, url: str, html: str) -> Iterator[dict]:
        soup = BeautifulSoup(html, "lxml")
        for entry in soup.select("ol.list-recent-jobs > li"):
            yield {
                "source": self.name,
                "source_url": url,
                "title": self._title(entry),
                "company": self._company(entry),
                "location": self._location(entry),
                "job_types": self._job_types(entry),
                "listing_url": self._listing_url(entry),
            }

    @staticmethod
    def _name_span(entry):
        return entry.select_one("span.listing-company-name")

    @classmethod
    def _title(cls, entry) -> str | None:
        link = entry.select_one("span.listing-company-name > a")
        if link is None:
            return None
        return link.get_text(strip=True) or None

    @classmethod
    def _company(cls, entry) -> str | None:
        """The company has no element of its own.

        It is a loose text node following the title anchor, so the only
        reliable handle is position within the parent span.
        """
        span = cls._name_span(entry)
        if span is None:
            return None
        seen_link = False
        for child in span.children:
            if getattr(child, "name", None) == "a":
                seen_link = True
                continue
            if seen_link and isinstance(child, NavigableString):
                text = " ".join(child.split())
                if text:
                    return text
        return None

    @classmethod
    def _location(cls, entry) -> str | None:
        el = entry.select_one("span.listing-location a")
        if el is None:
            return None
        return " ".join(el.get_text().split()) or None

    @classmethod
    def _job_types(cls, entry) -> list[str]:
        return [
            " ".join(a.get_text().split())
            for a in entry.select("span.listing-job-type a")
            if a.get_text(strip=True)
        ]

    @classmethod
    def _listing_url(cls, entry) -> str | None:
        link = entry.select_one("span.listing-company-name > a")
        if link is None or not link.get("href"):
            return None
        return urljoin(BASE, link["href"])

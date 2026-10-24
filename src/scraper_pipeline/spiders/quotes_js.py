"""Client-side rendered source.

This page ships an empty container and builds its content in JavaScript, so
fetching the HTML returns nothing useful. There are two ways to handle that, and
the order matters.

First, look for the data before reaching for a browser. This page holds its
records in a `var data = [...]` literal inside a script tag, so the records can
be read straight out of the source with no browser at all. That path is faster,
cheaper, and far more stable, because a JSON structure changes much less often
than rendered markup.

Second, fall back to driving a real browser when the data genuinely only exists
after execution. That is what `render_with_playwright` is for.

Reaching for Playwright first is the common mistake. It turns a 200ms request
into a multi-second browser launch and makes CI slow and flaky, usually to
extract something that was sitting in the page source the whole time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from .base import Spider

BASE = "https://quotes.toscrape.com/js/"

# The page embeds its records as a JavaScript array literal.
DATA_RE = re.compile(r"var\s+data\s*=\s*(\[.*?\]);", re.S)


def render_with_playwright(url: str, timeout_ms: int = 15000) -> str:
    """Return fully rendered HTML. Only used when static extraction fails.

    Playwright is an optional dependency so the core pipeline stays installable
    without a browser download.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Playwright is not installed. Install the browser extra: "
            "pip install -e '.[browser]' && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            page.wait_for_selector(".quote", timeout=timeout_ms)
            return page.content()
        finally:
            browser.close()


class QuotesJsSpider(Spider):
    name = "quotes_js"
    nullable_fields = {"tags"}

    def __init__(self, pages: int = 2, allow_browser: bool = True) -> None:
        self.pages = pages
        self.allow_browser = allow_browser

    def urls(self) -> Iterator[str]:
        for page in range(1, self.pages + 1):
            yield BASE if page == 1 else f"{BASE}page/{page}/"

    def parse(self, url: str, html: str) -> Iterator[dict]:
        records = self._from_embedded_json(html)
        if records is None:
            records = self._from_rendered_dom(html)
        for record in records:
            author = record.get("author") or {}
            yield {
                "source": self.name,
                "source_url": url,
                "text": record.get("text"),
                "author": author.get("name") if isinstance(author, dict) else author,
                "tags": record.get("tags") or [],
            }

    @staticmethod
    def _from_embedded_json(html: str) -> list[dict] | None:
        """Pull the records out of the script tag. The cheap, stable path."""
        match = DATA_RE.search(html)
        if not match:
            return None
        raw = match.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # The literal is JavaScript, not strict JSON: keys may be unquoted
            # and strings may use single quotes. Normalise the common cases.
            normalised = re.sub(r"(\w+)\s*:", r'"\1":', raw)
            normalised = normalised.replace("'", '"')
            try:
                return json.loads(normalised)
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _from_rendered_dom(html: str) -> list[dict]:
        """Read the DOM after rendering. The expensive fallback."""
        soup = BeautifulSoup(html, "lxml")
        out: list[dict] = []
        for quote in soup.select("div.quote"):
            text_el = quote.select_one("span.text")
            author_el = quote.select_one("small.author")
            out.append(
                {
                    "text": text_el.get_text(strip=True) if text_el else None,
                    "author": author_el.get_text(strip=True) if author_el else None,
                    "tags": [t.get_text(strip=True) for t in quote.select("a.tag")],
                }
            )
        return out

    def fetch(self, fetcher, url: str) -> str:
        html = fetcher.get(url).text
        if self._from_embedded_json(html) is not None:
            return html
        if not self.allow_browser:
            return html
        return render_with_playwright(url)

"""Parser regression tests, pinned to saved HTML.

The point of committing fixtures is that a selector can never break silently.
When the real site changes, the fix is to add the new HTML as a fixture here
first, so the thing that broke once cannot break again unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scraper_pipeline.drift import Severity, analyse, compute_null_rates
from scraper_pipeline.models import Book
from scraper_pipeline.spiders.books import BooksSpider
from scraper_pipeline.spiders.quotes_js import QuotesJsSpider

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://books.toscrape.com/catalogue/page-1.html"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestBooksParser:
    def test_extracts_every_product(self):
        items = list(BooksSpider().parse(URL, load("books_page_ok.html")))
        assert len(items) == 5

    def test_every_field_is_populated(self):
        items = list(BooksSpider().parse(URL, load("books_page_ok.html")))
        assert all(rate == 0.0 for rate in compute_null_rates(items).values())

    def test_items_satisfy_the_schema(self):
        for raw in BooksSpider().parse(URL, load("books_page_ok.html")):
            Book.model_validate(raw)

    def test_price_is_numeric_without_currency_symbol(self):
        first = next(iter(BooksSpider().parse(URL, load("books_page_ok.html"))))
        assert first["price"].replace(".", "").isdigit()

    def test_title_is_the_full_name_not_the_truncated_link_text(self):
        # The visible link text is ellipsised; the title attribute is not.
        titles = [i["title"] for i in BooksSpider().parse(URL, load("books_page_ok.html"))]
        assert not any(t.endswith("...") for t in titles)

    def test_rating_is_parsed_from_the_class_word(self):
        ratings = [i["rating"] for i in BooksSpider().parse(URL, load("books_page_ok.html"))]
        assert all(r is None or 1 <= r <= 5 for r in ratings)


class TestLayoutChangeDetection:
    """The scenario the application asked about, proven end to end."""

    def test_partial_layout_change_still_returns_items(self):
        # This is why yield alone is not enough: the count is unchanged.
        items = list(BooksSpider().parse(URL, load("books_page_layout_changed.html")))
        assert len(items) == 5

    def test_but_the_moved_selectors_show_up_as_null_fields(self):
        items = list(BooksSpider().parse(URL, load("books_page_layout_changed.html")))
        rates = compute_null_rates(items)
        assert rates["price"] == 1.0
        assert rates["rating"] == 1.0
        # Untouched selectors keep working, which localises the break.
        assert rates["title"] == 0.0
        assert rates["availability"] == 0.0

    def test_and_the_monitor_raises_critical(self):
        items = list(BooksSpider().parse(URL, load("books_page_layout_changed.html")))
        report = analyse(source="books", items=items, history=[5, 5, 5])
        assert report.severity is Severity.CRITICAL
        broken = {f.detail.get("field") for f in report.findings if f.code == "field_disappeared"}
        assert "price" in broken

    def test_total_layout_change_yields_nothing_and_is_critical(self):
        items = list(BooksSpider().parse(URL, load("books_page_empty.html")))
        assert items == []
        report = analyse(source="books", items=items, history=[5, 5, 5])
        assert report.severity is Severity.CRITICAL
        assert any(f.code == "zero_yield" for f in report.findings)

    def test_changed_items_fail_schema_validation(self):
        # Rejected rather than stored as half-empty rows, so bad data never
        # reaches whatever consumes this downstream.
        items = list(BooksSpider().parse(URL, load("books_page_layout_changed.html")))
        with pytest.raises(ValidationError):
            Book.model_validate(items[0])


class TestQuotesParser:
    def test_prefers_embedded_json_over_rendering(self):
        html = load("quotes_js_ok.html")
        assert QuotesJsSpider()._from_embedded_json(html) is not None

    def test_extracts_quotes_without_a_browser(self):
        html = load("quotes_js_ok.html")
        items = list(QuotesJsSpider().parse("https://quotes.toscrape.com/js/", html))
        assert len(items) == 10
        assert all(i["text"] and i["author"] for i in items)

    def test_falls_back_to_the_dom_when_no_json_is_present(self):
        rendered = """
        <html><body>
          <div class="quote">
            <span class="text">Rendered quote</span>
            <small class="author">Someone</small>
            <a class="tag">life</a>
          </div>
        </body></html>
        """
        items = list(QuotesJsSpider().parse("https://example.com", rendered))
        assert items[0]["text"] == "Rendered quote"
        assert items[0]["author"] == "Someone"
        assert items[0]["tags"] == ["life"]

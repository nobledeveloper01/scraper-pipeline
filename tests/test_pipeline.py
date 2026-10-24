"""End-to-end behaviour, with the network stubbed out.

These tests drive the real pipeline, storage and drift code. Only the HTTP
layer is replaced, so what is exercised here is what runs in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from scraper_pipeline.drift import Severity
from scraper_pipeline.pipeline import Pipeline
from scraper_pipeline.spiders.books import BooksSpider
from scraper_pipeline.storage import Repository, SnapshotArchive, init_db, make_engine

FIXTURES = Path(__file__).parent / "fixtures"


class StubSpider(BooksSpider):
    """A books spider that serves fixture HTML instead of making requests."""

    def __init__(self, fixture: str, pages: int = 1) -> None:
        super().__init__(pages=pages)
        self.fixture = fixture

    def urls(self) -> Iterator[str]:
        for page in range(1, self.pages + 1):
            yield f"https://books.toscrape.com/catalogue/page-{page}.html"

    def fetch(self, fetcher, url: str) -> str:
        return (FIXTURES / self.fixture).read_text(encoding="utf-8")


@pytest.fixture()
def pipeline(tmp_db, tmp_snapshots):
    engine = make_engine(tmp_db)
    init_db(engine)
    repo = Repository(engine)
    return Pipeline(repo, SnapshotArchive(tmp_snapshots), fetcher=object()), repo


class TestHappyPath:
    def test_stores_items_and_reports_healthy(self, pipeline):
        pipe, _ = pipeline
        result = pipe.run(StubSpider("books_page_ok.html"))
        assert result.stored == 5
        assert result.rejected == 0
        assert result.ok

    def test_archives_the_html_before_parsing(self, pipeline, tmp_snapshots):
        pipe, _ = pipeline
        result = pipe.run(StubSpider("books_page_ok.html"))
        run_dir = Path(tmp_snapshots) / "books" / result.run_id
        assert run_dir.exists()
        assert list(run_dir.glob("*.html"))
        assert (run_dir / "manifest.jsonl").exists()

    def test_records_the_run(self, pipeline):
        pipe, repo = pipeline
        pipe.run(StubSpider("books_page_ok.html"))
        assert repo.recent_yields("books", 5) == [5]
        assert repo.last_success("books") is not None


class TestBreakageIsCaught:
    def test_total_layout_change_fails_the_run(self, pipeline):
        pipe, _ = pipeline
        result = pipe.run(StubSpider("books_page_empty.html"))
        assert result.stored == 0
        assert not result.ok
        assert result.report.severity is Severity.CRITICAL

    def test_partial_change_is_caught_despite_normal_item_count(self, pipeline):
        """The failure an exit code cannot see.

        Three healthy runs first to establish a baseline, then a run against the
        changed layout. The item count is identical, so yield monitoring alone
        would call it healthy. The field null rate is what catches it.
        """
        pipe, _ = pipeline
        for _ in range(3):
            assert pipe.run(StubSpider("books_page_ok.html")).ok

        result = pipe.run(StubSpider("books_page_layout_changed.html"))

        assert result.report.yield_ratio == 1.0  # count unchanged
        assert not result.ok  # caught anyway
        assert result.report.field_null_rates["price"] == 1.0

    def test_broken_items_go_to_the_dead_letter_table_not_the_items_table(self, pipeline):
        pipe, _ = pipeline
        result = pipe.run(StubSpider("books_page_layout_changed.html"))
        assert result.stored == 0
        assert result.rejected == 5

    def test_a_failed_run_does_not_poison_the_baseline(self, pipeline):
        pipe, repo = pipeline
        for _ in range(3):
            pipe.run(StubSpider("books_page_ok.html"))
        pipe.run(StubSpider("books_page_empty.html"))
        # Only healthy runs count toward the baseline.
        assert repo.recent_yields("books", 10) == [5, 5, 5]


class TestMultiPage:
    def test_aggregates_across_pages(self, pipeline):
        pipe, _ = pipeline
        result = pipe.run(StubSpider("books_page_ok.html", pages=3))
        assert result.pages == 3
        assert result.stored == 15


class TestDeduplication:
    """Re-running must not duplicate rows, and must not blind the monitor.

    These two pull against each other, which is how the original bug happened:
    the fix for duplicates silently disabled yield monitoring.
    """

    def test_identical_reruns_do_not_duplicate_rows(self, pipeline):
        pipe, _ = pipeline
        first = pipe.run(StubSpider("books_page_ok.html"))
        second = pipe.run(StubSpider("books_page_ok.html"))
        assert first.stored == 5
        assert second.stored == 0  # nothing new written

    def test_but_the_run_still_reports_what_it_saw(self, pipeline):
        pipe, _ = pipeline
        pipe.run(StubSpider("books_page_ok.html"))
        second = pipe.run(StubSpider("books_page_ok.html"))
        assert second.seen == 5  # the scraper is working
        assert second.ok

    def test_baseline_survives_a_fully_deduplicated_source(self, pipeline):
        # The regression this guards: with the baseline built from rows written,
        # a stable source scores zero every run, no baseline ever forms, and
        # yield monitoring is quietly switched off forever.
        pipe, repo = pipeline
        for _ in range(3):
            pipe.run(StubSpider("books_page_ok.html"))
        assert repo.recent_yields("books", 5) == [5, 5, 5]

        fourth = pipe.run(StubSpider("books_page_ok.html"))
        assert fourth.report.baseline == 5.0
        assert fourth.report.yield_ratio == 1.0

    def test_breakage_is_still_caught_on_a_deduplicated_source(self, pipeline):
        pipe, _ = pipeline
        for _ in range(3):
            pipe.run(StubSpider("books_page_ok.html"))
        broken = pipe.run(StubSpider("books_page_empty.html"))
        assert not broken.ok
        assert broken.report.severity is Severity.CRITICAL

    def test_fingerprint_ignores_run_metadata(self):
        from scraper_pipeline.storage import fingerprint

        a = {"title": "X", "price": "1.00", "scraped_at": "2026-01-01T00:00:00Z"}
        b = {"title": "X", "price": "1.00", "scraped_at": "2026-06-30T12:00:00Z"}
        assert fingerprint(a) == fingerprint(b)

    def test_fingerprint_still_distinguishes_real_changes(self):
        from scraper_pipeline.storage import fingerprint

        a = {"title": "X", "price": "1.00"}
        b = {"title": "X", "price": "2.00"}
        assert fingerprint(a) != fingerprint(b)

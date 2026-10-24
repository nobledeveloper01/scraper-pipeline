"""The detection logic, tested against the failure modes it exists to catch."""

from __future__ import annotations

from scraper_pipeline.drift import Severity, analyse, baseline_from, compute_null_rates


def item(**overrides) -> dict:
    base = {
        "source": "books",
        "source_url": "https://example.com/1",
        "title": "A Book",
        "price": "51.77",
        "currency": "GBP",
        "availability": "In stock",
        "rating": 3,
    }
    base.update(overrides)
    return base


class TestNullRates:
    def test_healthy_items_have_no_nulls(self):
        rates = compute_null_rates([item(), item(), item()])
        assert all(rate == 0.0 for rate in rates.values())

    def test_identifies_the_single_broken_field(self):
        # One selector moved: price is gone, everything else still parses.
        items = [item(price=None) for _ in range(10)]
        rates = compute_null_rates(items)
        assert rates["price"] == 1.0
        assert rates["title"] == 0.0

    def test_whitespace_only_counts_as_missing(self):
        # A selector that matches the wrong element often yields empty strings
        # rather than None, and that must not look healthy.
        rates = compute_null_rates([item(title="   "), item(title="Real")])
        assert rates["title"] == 0.5

    def test_empty_collections_count_as_missing(self):
        rates = compute_null_rates([{"tags": []}, {"tags": ["x"]}])
        assert rates["tags"] == 0.5

    def test_field_absent_from_every_item_is_still_reported(self):
        # If a key vanishes entirely it must not disappear from the report.
        rates = compute_null_rates([{"a": 1}, {"a": 1, "b": 2}])
        assert rates["b"] == 0.5

    def test_metadata_fields_are_ignored(self):
        rates = compute_null_rates([item()])
        assert "source" not in rates
        assert "scraped_at" not in rates


class TestBaseline:
    def test_requires_minimum_history(self):
        assert baseline_from([40, 40], min_runs=3) is None

    def test_uses_median_not_mean(self):
        # One catastrophic run must not drag the baseline down and quietly
        # normalise a broken scraper.
        assert baseline_from([40, 40, 40, 1], min_runs=3) == 40.0

    def test_ignores_zero_runs(self):
        assert baseline_from([40, 0, 40, 40], min_runs=3) == 40.0


class TestAnalyse:
    def test_healthy_run_is_healthy(self):
        report = analyse(
            source="books",
            items=[item() for _ in range(40)],
            history=[40, 40, 40],
        )
        assert report.healthy
        assert report.severity is Severity.OK
        assert report.yield_ratio == 1.0

    def test_zero_items_is_critical_without_any_history(self):
        report = analyse(source="books", items=[], history=[])
        assert report.severity is Severity.CRITICAL
        assert any(f.code == "zero_yield" for f in report.findings)

    def test_yield_collapse_is_critical(self):
        report = analyse(
            source="books",
            items=[item() for _ in range(5)],
            history=[40, 40, 40],
        )
        assert report.severity is Severity.CRITICAL
        assert any(f.code == "yield_collapse" for f in report.findings)
        assert report.yield_ratio == 0.125

    def test_a_field_disappearing_is_critical_even_when_yield_is_normal(self):
        # This is the case exit codes and uptime checks miss entirely: the run
        # returns the usual number of items, every one of them missing a field.
        report = analyse(
            source="books",
            items=[item(price=None) for _ in range(40)],
            history=[40, 40, 40],
        )
        assert report.yield_ratio == 1.0
        assert report.severity is Severity.CRITICAL
        finding = next(f for f in report.findings if f.code == "field_disappeared")
        assert finding.detail["field"] == "price"

    def test_partial_degradation_warns_rather_than_pages(self):
        items = [item() for _ in range(6)] + [item(price=None) for _ in range(4)]
        report = analyse(source="books", items=items, history=[10, 10, 10])
        assert report.severity is Severity.WARNING
        assert any(f.code == "field_degraded" for f in report.findings)

    def test_degradation_below_threshold_is_tolerated(self):
        items = [item() for _ in range(9)] + [item(rating=None)]
        report = analyse(source="books", items=items, history=[10, 10, 10])
        assert report.healthy

    def test_declared_nullable_fields_never_alarm(self):
        report = analyse(
            source="books",
            items=[item(rating=None) for _ in range(40)],
            history=[40, 40, 40],
            nullable_fields={"rating"},
        )
        assert report.healthy

    def test_no_baseline_does_not_block_a_first_run(self):
        report = analyse(source="books", items=[item()], history=[])
        assert report.healthy
        assert any(f.code == "no_baseline" for f in report.findings)

    def test_growth_is_not_treated_as_a_problem(self):
        report = analyse(
            source="books",
            items=[item() for _ in range(120)],
            history=[40, 40, 40],
        )
        assert report.healthy

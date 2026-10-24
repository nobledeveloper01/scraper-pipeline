"""Prometheus metrics.

These are deliberately about output, not process health. A scraper that exits
zero having written nothing is the failure mode that matters, and an exit code
will never tell you about it.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

ITEMS_SCRAPED = Counter(
    "scraper_items_scraped_total",
    "Items that passed validation and were stored.",
    ["source"],
)

ITEMS_REJECTED = Counter(
    "scraper_items_rejected_total",
    "Items that failed schema validation and went to the dead-letter table.",
    ["source", "reason"],
)

PAGES_FETCHED = Counter(
    "scraper_pages_fetched_total",
    "HTTP responses received, labelled by status class.",
    ["source", "status_class"],
)

FETCH_ERRORS = Counter(
    "scraper_fetch_errors_total",
    "Requests that never produced a response (timeout, DNS, connection reset).",
    ["source", "kind"],
)

RUN_DURATION = Histogram(
    "scraper_run_duration_seconds",
    "Wall-clock duration of a complete run.",
    ["source"],
)

LAST_SUCCESS_TIMESTAMP = Gauge(
    "scraper_last_success_timestamp_seconds",
    "Unix time of the last run that finished healthy. Drives staleness alerts.",
    ["source"],
)

YIELD_RATIO = Gauge(
    "scraper_yield_ratio",
    "Items this run divided by the rolling median. 1.0 is normal, 0.0 is a dead scraper.",
    ["source"],
)

FIELD_NULL_RATE = Gauge(
    "scraper_field_null_rate",
    "Fraction of items in the last run where this field was missing or empty.",
    ["source", "field"],
)


def serve_metrics(port: int) -> None:
    """Expose /metrics for Prometheus to scrape."""
    start_http_server(port)


def status_class(code: int) -> str:
    """Bucket a status code so the label stays low cardinality."""
    return f"{code // 100}xx"

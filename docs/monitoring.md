# Monitoring

A scraper that exits zero having written nothing is the failure that matters.
Process health will never show it, so everything here measures output.

## Metrics

| Metric | Type | What it tells you |
|---|---|---|
| `scraper_items_scraped_total` | counter | Records stored, per source |
| `scraper_items_rejected_total` | counter | Schema failures, labelled by the field that failed |
| `scraper_pages_fetched_total` | counter | Responses, labelled by status class |
| `scraper_fetch_errors_total` | counter | Requests that never got a response |
| `scraper_run_duration_seconds` | histogram | Run wall clock |
| `scraper_last_success_timestamp_seconds` | gauge | Drives staleness alerts |
| `scraper_yield_ratio` | gauge | Items over rolling median |
| `scraper_field_null_rate` | gauge | Per field, per source |

Status codes are bucketed to `2xx`, `4xx`, `5xx` rather than recorded exactly,
to keep label cardinality bounded.

## Alert rules

```yaml
groups:
  - name: scrapers
    rules:
      - alert: ScraperReturnedNothing
        expr: scraper_yield_ratio == 0
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.source }} returned no items"

      - alert: ScraperYieldCollapse
        expr: scraper_yield_ratio < 0.5
        for: 15m
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.source }} at {{ $value | humanizePercentage }} of baseline"

      - alert: ScraperFieldDisappeared
        expr: scraper_field_null_rate >= 1
        for: 10m
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.source }}: '{{ $labels.field }}' empty on every record"

      - alert: ScraperFieldDegraded
        expr: scraper_field_null_rate > 0.3
        for: 30m
        labels: { severity: warning }

      - alert: ScraperStale
        expr: time() - scraper_last_success_timestamp_seconds > 86400
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.source }} has not had a healthy run in 24h"

      - alert: ScraperBlocked
        expr: rate(scraper_pages_fetched_total{status_class="4xx"}[15m]) > 0.3
        for: 10m
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.source }} is probably being blocked"
```

`ScraperStale` is the one that catches the failure nobody else does: a job that
stopped being scheduled at all. Every other alert needs a run to fire.

## Choosing thresholds

Defaults are a yield floor of 50% of the median and a null-rate ceiling of 30%.
Both are configurable per deployment.

Run with alerting in log-only mode for a fortnight before turning on paging.
Sources are seasonal, paginate inconsistently and have quiet days, and you want
to know the normal range before you decide what abnormal means.

Set thresholds from observed variance rather than intuition. A source whose
daily yield swings 40% needs a lower floor than one that returns exactly 1,000
records every day, and applying one number to both guarantees either noise or
blind spots.

## Logging

Structured JSON with a `run_id` on every line, so one run can be traced end to
end. The fields worth indexing are `run_id`, `source`, `event`, `status`.

## Dashboards

One row per source, four panels: yield ratio over time with the alert threshold
drawn on, null rate per field as a heatmap, status class distribution, and run
duration. The null-rate heatmap is the one that earns its place. A field drifting
from 2% to 15% over a month is a redesign in progress, and it is much cheaper to
fix before it reaches 100% than during the incident afterwards.

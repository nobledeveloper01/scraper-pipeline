# scraper-pipeline

A Python scraping pipeline that notices when it has broken.

Scrapers rarely fail loudly. The site changes its HTML, the request still returns
200, the parser still runs, and the job exits zero having written nothing, or
having written rows with half the fields empty. A process monitor sees a healthy
job. A dashboard sees stale data and nobody knows why.

This project treats that as the problem to solve. It monitors the output, not the
process.

## What it does

- Scrapes two sources, one server-rendered and one rendered in the browser.
- Validates every record against a schema, and keeps the failures instead of
  dropping them.
- Compares each run against what that source normally produces, and fails the run
  when the output looks wrong.
- Archives the raw HTML of every page, so when something breaks you can diff
  today's page against the last one that worked.
- Exposes Prometheus metrics, respects `robots.txt`, and rate limits itself.

## The two signals that matter

**Yield.** How many items came back, against the rolling median of recent healthy
runs. Zero items from a source that normally returns hundreds is an outage.

**Field null rate.** What fraction of records are missing each field. This is the
earlier and more precise signal. When `price` goes from 99% populated to 4% while
every other field holds, exactly one selector has moved and you already know
which one.

Yield alone is not enough, and the test suite proves it. In
`test_partial_change_is_caught_despite_normal_item_count`, a layout change
renames the price class. The scraper still returns the usual number of items, so
the yield ratio is exactly 1.0 and yield monitoring calls it healthy. The null
rate catches it.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"

scraper run books --pages 2      # static HTML source
scraper run quotes_js --pages 2  # client-side rendered source
scraper health                   # staleness per source
```

A run exits non-zero when the output is wrong, not only when the process
crashes, so it can be driven from cron or a CI schedule without extra glue.

```
$ scraper run books --pages 2
{
  "source": "books",
  "severity": "ok",
  "items": 40,
  "yield_ratio": 1.0,
  "baseline": 40.0,
  "field_null_rates": {
    "availability": 0.0, "currency": 0.0, "price": 0.0,
    "rating": 0.0, "title": 0.0
  },
  "findings": []
}
```

## When a scraper breaks

```bash
scraper diff books     # diff the archived HTML of the last two runs
scraper replay books   # re-parse the archived pages with the current parser
```

`diff` shows what changed on the page. `replay` lets you fix selectors and
confirm the fix against the exact pages that broke, without hammering the live
site while you iterate.

The full procedure is in [docs/layout-change-playbook.md](docs/layout-change-playbook.md).

## Monitoring

Prometheus metrics on `:9100` with `--metrics`. The ones that matter:

| Metric | Why |
|---|---|
| `scraper_yield_ratio` | Items this run over the rolling median. 1.0 normal, 0.0 dead |
| `scraper_field_null_rate` | Per field. Catches a moved selector before a human notices |
| `scraper_items_rejected_total` | Schema failures, labelled by the field that failed |
| `scraper_last_success_timestamp_seconds` | Drives staleness alerts |

Alert rules and thresholds are in [docs/monitoring.md](docs/monitoring.md).

## Incidents

[RUNBOOK.md](RUNBOOK.md) has the severity tiers, the first ten minutes of
triage, and the template for a status update.

## How it is put together

```
src/scraper_pipeline/
├── spiders/          one class per source: urls() and parse()
│   ├── base.py       fetching, retries, rate limiting, robots.txt
│   ├── books.py      static HTML via BeautifulSoup
│   └── quotes_js.py  client-side rendered, with a Playwright fallback
├── models.py         Pydantic schemas; validation is the first tripwire
├── drift.py          yield and null-rate analysis
├── storage.py        SQLAlchemy persistence and the HTML archive
├── pipeline.py       run orchestration
├── metrics.py        Prometheus metrics
├── alerting.py       severity to channel routing
└── cli.py            run, health, diff, replay
```

Adding a source means writing a `Spider` subclass with `urls()` and `parse()`,
and a Pydantic model. Everything else applies automatically.

### Getting the data without a browser first

`quotes_js` targets a page that builds its content in JavaScript, so fetching the
HTML returns an empty container. The obvious move is to reach for Playwright.
The better move is to look at the page source first: these records are sitting in
a `var data = [...]` literal in a script tag, and can be read straight out with
no browser at all.

That path is faster, survives CI, and is far more stable, because a JSON
structure changes much less often than rendered markup. Playwright is there as a
fallback for when the data genuinely only exists after execution, which is how it
should be used rather than as the default.

## Database

SQLite by default so it runs with no setup. Point `DATABASE_URL` at PostgreSQL
for anything real:

```bash
DATABASE_URL=postgresql+psycopg://user:pass@localhost/scraper scraper run books
docker compose up        # Postgres, Prometheus and the scraper together
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///scraper.db` | Where records go |
| `SNAPSHOT_DIR` | `snapshots` | Where archived HTML goes |
| `RATE_LIMIT_DELAY` | `1.0` | Seconds between requests to a host |
| `RESPECT_ROBOTS` | `true` | Check `robots.txt` before fetching |
| `MAX_RETRIES` | `3` | Retries on timeout or connection error |
| `ALERT_WEBHOOK_URL` | unset | Where alerts are posted |
| `METRICS_PORT` | `9100` | Prometheus port |

## Tests

```bash
pytest
```

40 tests. The parser tests are pinned to committed HTML fixtures, including a
deliberately altered page, so a selector cannot break silently. When the real
site changes, the fix starts by adding the new HTML as a fixture.

## Targets, and why these ones

`books.toscrape.com` and `quotes.toscrape.com` are published for this purpose, so
CI is reliable and nobody's terms of service are being bent to make a portfolio
piece. The spider layer is pluggable: pointing this at a commercial source is a
new `Spider` subclass and a schema, not a rewrite. The engineering here is the
monitoring and the recovery path, and neither depends on which site is behind it.

## Licence

MIT.

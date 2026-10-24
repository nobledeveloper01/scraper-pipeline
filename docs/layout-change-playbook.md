# A site changed its layout and the scraper returns nothing

The procedure behind `scraper diff` and `scraper replay`. Work through it in
order; each step rules something out.

## 1. Separate fetching from parsing

This is the fork that decides everything after it.

```bash
grep '"event":"run_finished"' logs | tail -5
```

A 403, 429 or captcha means we are blocked, and no amount of selector work will
help. A healthy 200 with a normal response size means the HTML arrived and the
parser is the problem. An unusually small 200 usually means a consent wall or a
login redirect, not the page you wanted.

## 2. Check the shape of the failure

```bash
scraper replay books
```

- **Zero items on every page.** The container selector moved. One fix.
- **Zero items on some pages.** A template variant you have not seen. Do not
  break the working pages fixing it.
- **Normal item count, fields empty.** A field selector moved. The null rates in
  the run report name it.

The third case is the one that gets missed, because the item count looks fine.

## 3. Diff against the last page that worked

```bash
scraper diff books
```

This is why every page is archived before parsing. Without it you are comparing
today's broken page against your memory of a working one.

## 4. Find where the data went

Check these in order, because the first two are cheaper and more stable.

**Is it in a script tag?** Many sites embed their records as JSON in the page
source. Read it there and you stop depending on markup entirely.

```bash
grep -o 'var data = \[.*\]' snapshots/books/<run>/*.html
grep -o 'application/ld+json' snapshots/books/<run>/*.html
```

**Is there an XHR endpoint?** Open the page in a browser with the network panel
and look for the request that populates it. Hitting the API directly is more
stable than scraping its output.

**Has it moved to client-side rendering?** Compare the archived HTML against
what the browser shows. If the content only exists after execution, a headless
browser is now required. Treat that as the last option, not the first.

## 5. Choose selectors that survive the next redesign

In order of preference:

1. A JSON-LD block or embedded JSON payload
2. A `data-*` attribute, usually added deliberately and left alone
3. An `id`
4. A meaningful class such as `product_pod` or `price_color`
5. Semantic structure, for example `article > h3 > a`

Avoid long CSS paths and anything positional. `div > div:nth-child(3) > p`
works the day you write it and breaks the first time someone adds a wrapper.

Return `None` when a field is not found. Never substitute a default, because a
default makes a broken selector look like healthy data and the null-rate monitor
goes blind.

## 6. Pin it with a test before deploying

```bash
cp snapshots/books/<run>/<page>.html tests/fixtures/books_page_<date>.html
pytest tests/test_parsers.py
```

A fix without a fixture is a fix that regresses silently. This is the step people
skip under time pressure, and it is the reason the same scraper breaks three
times a year.

## 7. Verify, then backfill

```bash
scraper replay books          # against the pages that broke
scraper run books --pages 1   # small live run
scraper run books             # full run
```

Confirm the yield ratio is back near 1.0 and the null rates are back near zero
before declaring it fixed. Then backfill the window that was missed, and replay
anything sitting in the dead-letter table.

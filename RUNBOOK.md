# Runbook

What to do when a scraper breaks, and what to tell people while you fix it.

## Severity

Routing matters as much as labelling. If every alert goes to the same channel,
the team learns to ignore all of them.

| Severity | Trigger | Route | Response |
|---|---|---|---|
| **Critical** | Zero items, or yield below 50% of baseline, or a field empty on every record | Page on-call | Immediate |
| **Warning** | A field empty on more than 30% of records, or rejects above 10% of a run | Ticket | Same working day |
| **Info** | New source with no baseline yet, or yield 50–80% of baseline | Dashboard | Review at standup |

A single degraded field is a ticket, not a page. One source down while the rest
work is a ticket. Everything down, or anything touching data a customer is
looking at right now, is a page.

## First ten minutes

Answer one question before anything else: **did we fail to fetch, or did we
fetch and fail to parse?** They have completely different causes, and guessing
wrong costs you the first half hour.

```bash
scraper health                      # which sources are stale, and since when
grep '"event":"run_finished"' logs  # status, counts, duration
```

Then look at the status codes for the failed run.

**403, 429, or a captcha page.** We are being blocked. This is not a parser
problem. Check whether the rate limit was raised recently, whether the user
agent changed, and whether the IP is on a block list. Back off first, diagnose
second.

**Timeouts or connection errors.** The site is down or unreachable. Confirm from
a second network before assuming it is us.

**200 with a normal response size.** The page arrived fine and the parser is the
problem. Go to the [layout change playbook](docs/layout-change-playbook.md).

**200 with an unusually small response.** Often an interstitial, a consent wall
or a login redirect rather than the page you expected. Read the archived HTML
before touching any selectors.

## Deciding whether to stop the scraper

Stop it if it is writing wrong data that something downstream will trust.
Rejected records are safe, because they go to the dead-letter table and never
reach the items table. Records that pass validation while being subtly wrong are
not safe, and that is the case to stop for.

Leave it running if it is simply returning nothing. An empty run is harmless and
keeps the archive and the metrics flowing while you work.

## After the fix

1. Add the HTML that broke as a test fixture, and a test that fails without the
   fix. The thing that broke once must not break again unnoticed.
2. `scraper replay <source>` against the archived pages to confirm.
3. Run against a small page count on the live site before a full run.
4. Backfill the window that was missed.
5. Check whether the same pattern exists in other spiders.

## Incident update template

Five lines, no jargon, and a committed time for the next update. Send the next
update at that time even if nothing has changed, because silence is what makes
people start asking around.

```
Issue: [what broke, which source, and when it started, with a timezone]
Impact: [what users actually see, and explicitly what is unaffected]
Action: [what is being done now, and whether a backfill will follow]
Status: [investigating | fix in progress | monitoring | resolved]
Next update: [a specific time, or "on resolution" if under 30 minutes away]
```

### Worked example

```
Issue: The Source A scraper began returning empty results at 09:15 WAT after a layout change on the source site.
Impact: Source A listings have not refreshed since 09:15. All other sources and the application itself are unaffected.
Action: We have identified the changed selectors, are deploying a parser fix, and will backfill the missed window once it lands.
Status: Fix in progress; no user action required.
Next update: 13:00 WAT, or sooner if the fix completes before then.
```

Say what is unaffected. Most of the anxiety in an incident comes from people not
knowing how far the problem reaches, and one clause fixes that.

## Recurring maintenance

Scrapers are not finished when they work; they are maintained until they are
retired.

- **Weekly:** review null-rate trends. A field drifting from 2% to 15% over a
  month is a redesign in progress, and it is cheaper to fix before it hits 100%.
- **Weekly:** check the dead-letter table for patterns rather than one-offs.
- **Monthly:** re-read `robots.txt` on every source. Permissions change quietly.
- **Monthly:** prune the HTML archive. It grows quickly and you only need enough
  history to diff against.

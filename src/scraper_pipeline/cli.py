"""Command line entry points."""

from __future__ import annotations

import sys
from pathlib import Path

import structlog
import typer

from .alerting import dispatch, to_json
from .config import settings
from .drift import Severity
from .metrics import serve_metrics
from .pipeline import Pipeline
from .spiders import REGISTRY
from .storage import Repository, SnapshotArchive, init_db, make_engine

app = typer.Typer(add_completion=False, help="Scraping pipeline with output-quality monitoring.")

# Logs go to stderr so stdout carries only the run report. That keeps
# `scraper run books | jq` working, which is how this gets used in a pipeline.
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)


@app.command()
def run(
    source: str = typer.Argument(..., help=f"One of: {', '.join(REGISTRY)}"),
    pages: int = typer.Option(2, help="Pages to fetch."),
    metrics: bool = typer.Option(False, help="Serve Prometheus metrics during the run."),
    no_browser: bool = typer.Option(False, help="Never fall back to Playwright."),
) -> None:
    """Run one source and exit non-zero if the output looks wrong."""
    if source not in REGISTRY:
        typer.echo(f"Unknown source '{source}'. Available: {', '.join(REGISTRY)}", err=True)
        raise typer.Exit(2)

    if metrics:
        serve_metrics(settings.metrics_port)

    engine = make_engine()
    init_db(engine)

    spider_cls = REGISTRY[source]
    spider = (
        spider_cls(pages=pages, allow_browser=not no_browser)
        if source == "quotes_js"
        else spider_cls(pages=pages)
    )

    result = Pipeline(Repository(engine), SnapshotArchive()).run(spider)
    typer.echo(to_json(result.report))
    dispatch(result.report)

    # Exit code reflects output quality, not whether the process crashed. This
    # is what makes the pipeline usable from cron or a CI schedule.
    if result.report.severity is Severity.CRITICAL:
        raise typer.Exit(1)


@app.command()
def health() -> None:
    """Report staleness per source, for an external uptime check to poll."""
    engine = make_engine()
    init_db(engine)
    repo = Repository(engine)
    stale = False
    for name in REGISTRY:
        last = repo.last_success(name)
        typer.echo(f"{name}: last success {last.isoformat() if last else 'never'}")
        if last is None:
            stale = True
    raise typer.Exit(1 if stale else 0)


@app.command()
def diff(
    source: str = typer.Argument(..., help="Source whose last two runs to compare."),
) -> None:
    """Diff the archived HTML of the two most recent runs.

    This is the first command to reach for when a parser starts returning
    nothing: it shows what actually changed on the page.
    """
    runs = SnapshotArchive().latest_runs(source, limit=2)
    if len(runs) < 2:
        typer.echo(f"Need two archived runs for '{source}', found {len(runs)}.", err=True)
        raise typer.Exit(2)

    newer, older = runs[0], runs[1]
    typer.echo(f"comparing {older.name} -> {newer.name}\n")

    import difflib

    for new_file in sorted(newer.glob("*.html")):
        old_file = older / new_file.name
        if not old_file.exists():
            typer.echo(f"{new_file.name}: no counterpart in previous run")
            continue
        old_lines = old_file.read_text(encoding="utf-8").splitlines()
        new_lines = new_file.read_text(encoding="utf-8").splitlines()
        delta = list(
            difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after", n=1)
        )
        if not delta:
            typer.echo(f"{new_file.name}: identical")
        else:
            typer.echo(f"{new_file.name}: {len(delta)} changed lines")
            for line in delta[:40]:
                typer.echo("  " + line)


@app.command()
def replay(
    source: str = typer.Argument(..., help="Source to re-parse."),
    run_dir: str = typer.Option(None, help="Specific archived run directory."),
) -> None:
    """Re-parse archived HTML with the current parser, without re-fetching.

    Use this after fixing selectors to confirm the fix works against the pages
    that broke, before pointing the scraper back at the live site.
    """
    archive = SnapshotArchive()
    target = Path(run_dir) if run_dir else (archive.latest_runs(source, 1) or [None])[0]
    if target is None or not target.exists():
        typer.echo(f"No archived run found for '{source}'.", err=True)
        raise typer.Exit(2)

    spider = REGISTRY[source]()
    total = 0
    for page in sorted(target.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        items = list(spider.parse(f"file://{page}", html))
        total += len(items)
        typer.echo(f"{page.name}: {len(items)} items")
    typer.echo(f"\ntotal: {total} items from {target}")
    if total == 0:
        raise typer.Exit(1)


def main() -> None:  # pragma: no cover
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    main()

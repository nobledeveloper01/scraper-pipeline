"""Run orchestration.

The order matters: archive the page before parsing it, validate every item,
keep the rejects, then judge the whole run against history before declaring
success. A run only counts as healthy if the output looks like the output this
source normally produces.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from .config import settings
from .drift import DriftReport, Severity, analyse
from .metrics import (
    FIELD_NULL_RATE,
    ITEMS_REJECTED,
    ITEMS_SCRAPED,
    LAST_SUCCESS_TIMESTAMP,
    RUN_DURATION,
    YIELD_RATIO,
)
from .models import Book, PythonJob, Quote, RejectedItem
from .spiders.base import Fetcher, Spider
from .storage import Repository, SnapshotArchive

log = structlog.get_logger()

SCHEMAS = {"books": Book, "quotes_js": Quote, "pyjobs": PythonJob}


@dataclass
class RunResult:
    run_id: str
    source: str
    stored: int
    rejected: int
    pages: int
    report: DriftReport
    duration: float

    @property
    def ok(self) -> bool:
        return self.report.healthy


class Pipeline:
    def __init__(
        self,
        repo: Repository,
        archive: SnapshotArchive | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.repo = repo
        self.archive = archive or SnapshotArchive()
        self._fetcher = fetcher

    def run(self, spider: Spider) -> RunResult:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        fetcher = self._fetcher or Fetcher(spider.name)
        schema = SCHEMAS[spider.name]

        self.repo.start_run(run_id, spider.name)
        log.info("run_started", run_id=run_id, source=spider.name)

        raw_items: list[dict] = []
        valid: list = []
        rejects: list[RejectedItem] = []
        pages = 0

        try:
            for url in spider.urls():
                html = spider.fetch(fetcher, url)
                # Archive first. If parsing explodes we still have the evidence.
                self.archive.save(run_id, spider.name, url, html)
                pages += 1

                for raw in spider.parse(url, html):
                    raw_items.append(raw)
                    try:
                        valid.append(schema.model_validate(raw))
                    except ValidationError as exc:
                        reason = exc.errors()[0]["loc"][0] if exc.errors() else "unknown"
                        ITEMS_REJECTED.labels(spider.name, str(reason)).inc()
                        rejects.append(
                            RejectedItem(
                                source=spider.name,
                                source_url=url,
                                raw=raw,
                                error=str(exc),
                                run_id=run_id,
                            )
                        )
        finally:
            if self._fetcher is None:
                fetcher.close()

        stored = self.repo.store_items(run_id, valid)
        self.repo.store_rejects(rejects)
        ITEMS_SCRAPED.labels(spider.name).inc(stored)

        # Judge the run against what this source normally does. Note that this
        # looks at every item the parser produced, valid or not, because a run
        # where everything failed validation is exactly the case we must catch.
        history = list(self.repo.recent_yields(spider.name, settings.thresholds.baseline_runs))
        report = analyse(
            source=spider.name,
            items=raw_items,
            history=history,
            yield_floor_ratio=settings.thresholds.yield_floor_ratio,
            null_rate_ceiling=settings.thresholds.null_rate_ceiling,
            min_runs_for_baseline=settings.thresholds.min_runs_for_baseline,
            nullable_fields=spider.nullable_fields,
        )

        duration = time.monotonic() - started
        RUN_DURATION.labels(spider.name).observe(duration)
        if report.yield_ratio is not None:
            YIELD_RATIO.labels(spider.name).set(report.yield_ratio)
        for name, rate in report.field_null_rates.items():
            FIELD_NULL_RATE.labels(spider.name, name).set(rate)

        status = "ok" if report.healthy else report.severity.value
        if report.healthy:
            LAST_SUCCESS_TIMESTAMP.labels(spider.name).set(time.time())

        self.repo.finish_run(
            run_id,
            items_stored=stored,
            items_rejected=len(rejects),
            pages_fetched=pages,
            status=status,
            yield_ratio=report.yield_ratio,
            notes="; ".join(f.message for f in report.findings) or None,
        )

        log.info(
            "run_finished",
            run_id=run_id,
            source=spider.name,
            stored=stored,
            rejected=len(rejects),
            pages=pages,
            status=status,
            duration=round(duration, 2),
        )
        for finding in report.findings:
            if finding.severity is Severity.CRITICAL:
                log.error("drift", code=finding.code, message=finding.message, **finding.detail)
            elif finding.severity is Severity.WARNING:
                log.warning("drift", code=finding.code, message=finding.message, **finding.detail)

        return RunResult(run_id, spider.name, stored, len(rejects), pages, report, duration)

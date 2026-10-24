"""Persistence, plus the raw HTML archive.

The archive is the part people skip and then regret. When a parser silently
starts returning nothing, the only fast way to find out what changed is to diff
today's HTML against the last run that worked. You cannot do that after the
fact, so every run writes its pages to disk before parsing them.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import settings
from .models import RejectedItem, ScrapedItem


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items_stored: Mapped[int] = mapped_column(Integer, default=0)
    items_rejected: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")
    yield_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text)
    rejected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_engine(url: str | None = None):
    """Build an engine, reading DATABASE_URL at call time.

    Deliberately not relying on the import-time settings object. Config read
    once at import cannot be changed by the process that imports it, which
    makes the CLI untestable and surprises anyone who sets an environment
    variable in a wrapper script.
    """
    return create_engine(url or os.getenv("DATABASE_URL") or settings.database_url, future=True)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def fingerprint(payload: dict) -> str:
    """Stable hash of an item, so re-runs do not create duplicates."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class SnapshotArchive:
    """Writes every fetched page to disk, keyed by run."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base = Path(base_dir or os.getenv("SNAPSHOT_DIR") or settings.snapshot_dir)

    def save(self, run_id: str, source: str, url: str, html: str) -> Path:
        target_dir = self.base / source / run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".html"
        path = target_dir / name
        path.write_text(html, encoding="utf-8")
        (target_dir / "manifest.jsonl").open("a", encoding="utf-8").write(
            json.dumps({"url": url, "file": name, "bytes": len(html)}) + "\n"
        )
        return path

    def latest_runs(self, source: str, limit: int = 2) -> list[Path]:
        source_dir = self.base / source
        if not source_dir.exists():
            return []
        runs = sorted(
            (p for p in source_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return runs[:limit]


class Repository:
    """All database access goes through here."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def start_run(self, run_id: str, source: str) -> None:
        with Session(self.engine) as s:
            s.add(Run(run_id=run_id, source=source, started_at=datetime.now(UTC)))
            s.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        items_stored: int,
        items_rejected: int,
        pages_fetched: int,
        status: str,
        yield_ratio: float | None = None,
        notes: str | None = None,
    ) -> None:
        with Session(self.engine) as s:
            run = s.scalar(select(Run).where(Run.run_id == run_id))
            if run is None:
                return
            run.finished_at = datetime.now(UTC)
            run.items_stored = items_stored
            run.items_rejected = items_rejected
            run.pages_fetched = pages_fetched
            run.status = status
            run.yield_ratio = yield_ratio
            run.notes = notes
            s.commit()

    def store_items(self, run_id: str, items: Iterable[ScrapedItem]) -> int:
        stored = 0
        with Session(self.engine) as s:
            for item in items:
                payload = item.model_dump(mode="json")
                s.add(
                    Item(
                        run_id=run_id,
                        source=item.source,
                        fingerprint=fingerprint(payload),
                        payload=payload,
                        scraped_at=item.scraped_at,
                    )
                )
                stored += 1
            s.commit()
        return stored

    def store_rejects(self, rejects: Iterable[RejectedItem]) -> int:
        count = 0
        with Session(self.engine) as s:
            for r in rejects:
                s.add(
                    DeadLetter(
                        run_id=r.run_id,
                        source=r.source,
                        source_url=r.source_url,
                        raw=r.raw,
                        error=r.error,
                        rejected_at=r.rejected_at,
                    )
                )
                count += 1
            s.commit()
        return count

    def recent_yields(self, source: str, limit: int) -> Sequence[int]:
        """Item counts from the most recent healthy runs, newest first."""
        with Session(self.engine) as s:
            rows = s.scalars(
                select(Run)
                .where(Run.source == source, Run.status == "ok")
                .order_by(Run.id.desc())
                .limit(limit)
            ).all()
        return [r.items_stored for r in rows]

    def last_success(self, source: str) -> datetime | None:
        with Session(self.engine) as s:
            run = s.scalar(
                select(Run)
                .where(Run.source == source, Run.status == "ok")
                .order_by(Run.id.desc())
                .limit(1)
            )
        return run.finished_at if run else None

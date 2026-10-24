"""Runtime settings.

Everything here can be overridden with an environment variable so the same image
runs locally against SQLite and in production against PostgreSQL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class DriftThresholds:
    """When output quality counts as a problem.

    `yield_floor_ratio` is the fraction of the rolling median below which a run
    is considered degraded. 0.5 means "this run returned less than half of what
    this source normally returns".

    `null_rate_ceiling` is the per-field null rate that counts as a broken
    selector. A field normally 98% populated dropping past this is the earliest
    reliable signal of a layout change.
    """

    yield_floor_ratio: float = 0.5
    null_rate_ceiling: float = 0.30
    baseline_runs: int = 7
    min_runs_for_baseline: int = 3


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///scraper.db")
    )
    snapshot_dir: str = field(
        default_factory=lambda: os.getenv("SNAPSHOT_DIR", "snapshots")
    )
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "USER_AGENT",
            "scraper-pipeline/0.1 (+https://github.com/nobledeveloper01/scraper-pipeline)",
        )
    )
    request_timeout: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT", 20.0))
    rate_limit_delay: float = field(default_factory=lambda: _env_float("RATE_LIMIT_DELAY", 1.0))
    max_retries: int = field(default_factory=lambda: _env_int("MAX_RETRIES", 3))
    respect_robots: bool = field(
        default_factory=lambda: os.getenv("RESPECT_ROBOTS", "true").lower() != "false"
    )
    metrics_port: int = field(default_factory=lambda: _env_int("METRICS_PORT", 9100))
    thresholds: DriftThresholds = field(default_factory=DriftThresholds)


settings = Settings()

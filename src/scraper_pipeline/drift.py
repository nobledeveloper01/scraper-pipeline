"""Detecting that a scraper has broken while still reporting success.

A scraper almost never fails loudly. The site changes, the request still returns
200, the parser still runs, and the job exits zero having written nothing or
having written records with half the fields empty. Exit codes and uptime checks
are blind to all of it.

So this module ignores process health entirely and looks at two properties of
the output:

1. Yield. How many items came back, against what this source normally returns.
   A collapse to zero is an outage. A drop to a third is usually a template
   variant that stopped matching.

2. Field null rate. What fraction of items are missing each field. This is the
   earlier and more precise signal: when `price` goes from 99% populated to 4%
   while everything else holds, exactly one selector has moved, and you know
   which one before a human notices anything.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class DriftReport:
    source: str
    items: int
    yield_ratio: float | None
    baseline: float | None
    field_null_rates: dict[str, float]
    findings: list[Finding]

    @property
    def severity(self) -> Severity:
        if any(f.severity is Severity.CRITICAL for f in self.findings):
            return Severity.CRITICAL
        if any(f.severity is Severity.WARNING for f in self.findings):
            return Severity.WARNING
        return Severity.OK

    @property
    def healthy(self) -> bool:
        return self.severity is Severity.OK


def compute_null_rates(items: list[dict], ignore: set[str] | None = None) -> dict[str, float]:
    """Fraction of items where each field is missing, None, or empty.

    Looks across the union of keys, not just the keys of the first item, so a
    field that vanished entirely still shows up at 100%.
    """
    ignore = ignore or {"scraped_at", "source", "source_url"}
    if not items:
        return {}

    keys: set[str] = set()
    for item in items:
        keys.update(item.keys())
    keys -= ignore

    rates: dict[str, float] = {}
    total = len(items)
    for key in sorted(keys):
        missing = 0
        for item in items:
            value = item.get(key)
            if value is None:
                missing += 1
            elif isinstance(value, str) and not value.strip():
                missing += 1
            elif isinstance(value, list | dict) and len(value) == 0:
                missing += 1
        rates[key] = missing / total
    return rates


def baseline_from(history: list[int], min_runs: int) -> float | None:
    """Median of recent healthy runs.

    Median rather than mean because one bad run should not drag the baseline
    down and quietly normalise a broken scraper.
    """
    usable = [h for h in history if h > 0]
    if len(usable) < min_runs:
        return None
    return float(statistics.median(usable))


def analyse(
    *,
    source: str,
    items: list[dict],
    history: list[int],
    yield_floor_ratio: float = 0.5,
    null_rate_ceiling: float = 0.30,
    min_runs_for_baseline: int = 3,
    nullable_fields: set[str] | None = None,
) -> DriftReport:
    """Judge a completed run against what this source normally produces."""
    nullable_fields = nullable_fields or set()
    count = len(items)
    baseline = baseline_from(history, min_runs_for_baseline)
    ratio = (count / baseline) if baseline else None
    findings: list[Finding] = []

    # Zero items is unambiguous, and does not need a baseline to be alarming.
    if count == 0:
        findings.append(
            Finding(
                Severity.CRITICAL,
                "zero_yield",
                f"{source} returned no items at all.",
                {"items": 0},
            )
        )
    elif ratio is not None and ratio < yield_floor_ratio:
        findings.append(
            Finding(
                Severity.CRITICAL,
                "yield_collapse",
                (
                    f"{source} returned {count} items against a baseline of "
                    f"{baseline:.0f} ({ratio:.0%} of normal)."
                ),
                {"items": count, "baseline": baseline, "ratio": ratio},
            )
        )
    elif baseline is None:
        findings.append(
            Finding(
                Severity.OK,
                "no_baseline",
                f"{source} has fewer than {min_runs_for_baseline} healthy runs; "
                "yield checking is not active yet.",
                {"items": count},
            )
        )

    null_rates = compute_null_rates(items)
    for name, rate in null_rates.items():
        if name in nullable_fields:
            continue
        if rate >= 1.0:
            findings.append(
                Finding(
                    Severity.CRITICAL,
                    "field_disappeared",
                    f"'{name}' is empty on every item. Its selector has almost certainly moved.",
                    {"field": name, "null_rate": rate},
                )
            )
        elif rate > null_rate_ceiling:
            findings.append(
                Finding(
                    Severity.WARNING,
                    "field_degraded",
                    (
                        f"'{name}' is empty on {rate:.0%} of items, above the "
                        f"{null_rate_ceiling:.0%} threshold."
                    ),
                    {"field": name, "null_rate": rate},
                )
            )

    return DriftReport(
        source=source,
        items=count,
        yield_ratio=ratio,
        baseline=baseline,
        field_null_rates=null_rates,
        findings=findings,
    )

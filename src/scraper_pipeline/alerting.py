"""Turning findings into something a human is told about.

Severity maps to channel, not just to colour. A dead source pages whoever is on
call; one degraded field raises a ticket. Routing everything to the same place
is how teams learn to ignore alerts.
"""

from __future__ import annotations

import json
import os

import httpx
import structlog

from .drift import DriftReport, Severity

log = structlog.get_logger()


def format_alert(report: DriftReport) -> str:
    lines = [f"[{report.severity.value.upper()}] {report.source}: {report.items} items"]
    if report.yield_ratio is not None:
        lines.append(f"yield {report.yield_ratio:.0%} of baseline ({report.baseline:.0f})")
    for finding in report.findings:
        if finding.severity is not Severity.OK:
            lines.append(f"  - {finding.message}")
    return "\n".join(lines)


def dispatch(report: DriftReport, webhook_url: str | None = None) -> bool:
    """Send an alert if the run warrants one. Returns whether anything was sent."""
    if report.healthy:
        return False

    message = format_alert(report)
    url = webhook_url or os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        log.warning("alert_not_sent", reason="no ALERT_WEBHOOK_URL configured", alert=message)
        return False

    try:
        httpx.post(url, json={"text": message}, timeout=10.0).raise_for_status()
        log.info("alert_sent", severity=report.severity.value, source=report.source)
        return True
    except httpx.HTTPError as exc:
        # An alerting failure must never take the pipeline down with it.
        log.error("alert_failed", error=str(exc), alert=message)
        return False


def to_json(report: DriftReport) -> str:
    return json.dumps(
        {
            "source": report.source,
            "severity": report.severity.value,
            "items": report.items,
            "yield_ratio": report.yield_ratio,
            "baseline": report.baseline,
            "field_null_rates": report.field_null_rates,
            "findings": [
                {"severity": f.severity.value, "code": f.code, "message": f.message, **f.detail}
                for f in report.findings
            ],
        },
        indent=2,
    )

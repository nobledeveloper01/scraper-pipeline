"""Alerting behaviour.

The rule that matters here: a failure in the alerting path must never take the
pipeline down with it. A scraper that crashes because Slack was unreachable is
worse than the problem it was trying to report.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from scraper_pipeline.alerting import dispatch, format_alert, to_json
from scraper_pipeline.drift import analyse

WEBHOOK = "https://hooks.example.com/services/T000/B000/XXXX"


def healthy_report():
    return analyse(source="books", items=[{"title": "A", "price": "1.00"}], history=[1, 1, 1])


def broken_report():
    return analyse(source="books", items=[], history=[40, 40, 40])


class TestFormatting:
    def test_names_the_source_and_severity(self):
        text = format_alert(broken_report())
        assert "CRITICAL" in text
        assert "books" in text

    def test_includes_each_finding(self):
        text = format_alert(broken_report())
        assert "returned no items" in text

    def test_json_output_is_machine_readable(self):
        payload = json.loads(to_json(broken_report()))
        assert payload["severity"] == "critical"
        assert payload["items"] == 0
        assert payload["findings"]


class TestDispatch:
    def test_healthy_runs_do_not_alert(self):
        assert dispatch(healthy_report(), WEBHOOK) is False

    @respx.mock
    def test_critical_runs_post_to_the_webhook(self):
        route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200))
        assert dispatch(broken_report(), WEBHOOK) is True
        assert route.called
        assert "CRITICAL" in json.loads(route.calls[0].request.content)["text"]

    def test_missing_webhook_is_logged_not_raised(self, monkeypatch):
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
        assert dispatch(broken_report()) is False

    @respx.mock
    def test_a_webhook_failure_never_propagates(self):
        respx.post(WEBHOOK).mock(return_value=httpx.Response(500))
        # Must return False rather than raising. The pipeline has already done
        # its job by this point and must not die reporting that it worked.
        assert dispatch(broken_report(), WEBHOOK) is False

    @respx.mock
    def test_a_network_error_never_propagates(self):
        respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("unreachable"))
        assert dispatch(broken_report(), WEBHOOK) is False


class TestReportShape:
    @pytest.mark.parametrize(
        "items,history,expected",
        [
            ([], [], "critical"),
            ([{"a": 1}], [], "ok"),
            ([{"a": 1}] * 5, [40, 40, 40], "critical"),
        ],
    )
    def test_severity_mapping(self, items, history, expected):
        report = analyse(source="books", items=items, history=history)
        assert report.severity.value == expected

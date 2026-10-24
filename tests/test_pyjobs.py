"""Parser tests against a real production page.

The fixture here is a slice of www.python.org/jobs as it was actually served,
not a tidied-up version. It keeps the inconsistent whitespace, the stray tab in
the company name and the loose text nodes, because those are the conditions the
parser has to survive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scraper_pipeline.drift import Severity, analyse, compute_null_rates
from scraper_pipeline.models import PythonJob
from scraper_pipeline.spiders.pyjobs import PyJobsSpider

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.python.org/jobs/"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def items(fixture: str = "pyjobs_ok.html") -> list[dict]:
    return list(PyJobsSpider().parse(URL, load(fixture)))


class TestExtraction:
    def test_finds_every_posting(self):
        assert len(items()) == 4

    def test_no_field_is_empty(self):
        assert all(rate == 0.0 for rate in compute_null_rates(items()).values())

    def test_items_satisfy_the_schema(self):
        for raw in items():
            PythonJob.model_validate(raw)

    def test_company_is_extracted_from_a_loose_text_node(self):
        # The company has no element of its own: it is a bare text node after
        # the title anchor and a <br/>. This is the assertion that would break
        # if someone "simplified" the parser into a CSS selector.
        companies = [i["company"] for i in items()]
        assert all(c and c.strip() == c for c in companies)
        assert not any("\n" in c or "\t" in c for c in companies)

    def test_title_and_company_are_not_the_same_value(self):
        # The easy mistake here is grabbing the anchor text for both.
        for item in items():
            assert item["title"] != item["company"]

    def test_listing_url_is_absolute(self):
        for item in items():
            assert item["listing_url"].startswith("https://www.python.org/jobs/")

    def test_job_types_is_a_clean_list(self):
        for item in items():
            assert isinstance(item["job_types"], list)
            assert all(t == t.strip() and t for t in item["job_types"])

    def test_whitespace_is_normalised_in_location(self):
        for item in items():
            if item["location"]:
                assert "  " not in item["location"]
                assert item["location"].strip() == item["location"]


class TestLayoutChange:
    def test_renamed_container_takes_out_title_and_company(self):
        changed = items("pyjobs_layout_changed.html")
        rates = compute_null_rates(changed)
        assert rates["title"] == 1.0
        assert rates["company"] == 1.0
        # Location and job types sit in different spans and still resolve,
        # which is what localises the break to one selector.
        assert rates["location"] == 0.0

    def test_monitor_flags_it_as_critical(self):
        report = analyse(
            source="pyjobs",
            items=items("pyjobs_layout_changed.html"),
            history=[22, 22, 22],
            nullable_fields=PyJobsSpider.nullable_fields,
        )
        assert report.severity is Severity.CRITICAL
        broken = {f.detail.get("field") for f in report.findings if f.code == "field_disappeared"}
        assert {"title", "company"} <= broken

    def test_broken_items_fail_validation(self):
        with pytest.raises(ValidationError):
            PythonJob.model_validate(items("pyjobs_layout_changed.html")[0])


class TestNullableFields:
    def test_missing_location_does_not_alarm(self):
        # Not every posting has a location, and that is normal for this source
        # rather than a sign the parser broke.
        rows = [dict(i, location=None) for i in items()]
        report = analyse(
            source="pyjobs",
            items=rows,
            history=[4, 4, 4],
            nullable_fields=PyJobsSpider.nullable_fields,
        )
        assert report.healthy

"""CLI behaviour.

The important assertion is the exit code: a run whose output looks wrong must
exit non-zero, because that is what makes this usable from cron or CI without
extra glue.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from scraper_pipeline.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


class TestRun:
    def test_unknown_source_exits_two(self):
        result = runner.invoke(app, ["run", "nope"])
        assert result.exit_code == 2
        assert "Unknown source" in result.output

    def test_lists_available_sources_on_error(self):
        result = runner.invoke(app, ["run", "nope"])
        assert "books" in result.output


class TestHealth:
    def test_reports_never_run_sources(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'h.db'}")
        result = runner.invoke(app, ["health"])
        assert "never" in result.output
        assert result.exit_code == 1


class TestDiff:
    def test_requires_two_archived_runs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
        result = runner.invoke(app, ["diff", "books"])
        assert result.exit_code == 2
        assert "Need two archived runs" in result.output


class TestReplay:
    def test_reparses_archived_html(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "books" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "page.html").write_text(
            (FIXTURES / "books_page_ok.html").read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
        result = runner.invoke(app, ["replay", "books", "--run-dir", str(run_dir)])
        assert result.exit_code == 0
        assert "5 items" in result.output

    def test_exits_non_zero_when_the_parser_finds_nothing(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "books" / "run-2"
        run_dir.mkdir(parents=True)
        (run_dir / "page.html").write_text(
            (FIXTURES / "books_page_empty.html").read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
        result = runner.invoke(app, ["replay", "books", "--run-dir", str(run_dir)])
        assert result.exit_code == 1

    def test_missing_archive_exits_two(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
        result = runner.invoke(app, ["replay", "books"])
        assert result.exit_code == 2

"""The whole pipeline against an authenticated source.

Login, fetch, validate, store, monitor, with a token that dies mid-run.
"""

from __future__ import annotations

import pytest

from scraper_pipeline.auth import BearerTokenAuth
from scraper_pipeline.pipeline import Pipeline
from scraper_pipeline.spiders.base import Fetcher
from scraper_pipeline.spiders.listings import ListingsSpider
from scraper_pipeline.storage import Repository, SnapshotArchive, init_db, make_engine
from tests.authserver import VALID_PASSWORD, VALID_USER, AuthServer


@pytest.fixture()
def server():
    with AuthServer() as s:
        yield s


@pytest.fixture()
def setup(server, tmp_db, tmp_snapshots):
    engine = make_engine(tmp_db)
    init_db(engine)
    auth = BearerTokenAuth(
        login_url=f"{server.base_url}/auth/login",
        refresh_url=f"{server.base_url}/auth/refresh",
        username=VALID_USER,
        password=VALID_PASSWORD,
    )
    fetcher = Fetcher("listings", auth=auth)
    pipe = Pipeline(Repository(engine), SnapshotArchive(tmp_snapshots), fetcher=fetcher)
    spider = ListingsSpider(pages=1, base_url=server.base_url)
    yield pipe, spider, server, Repository(engine)
    fetcher.close()


class TestAuthenticatedRun:
    def test_scrapes_a_protected_source_end_to_end(self, setup):
        pipe, spider, server, _ = setup
        result = pipe.run(spider)
        assert result.stored == 4
        assert result.rejected == 0
        assert result.ok
        assert server.state.login_calls == 1

    def test_records_are_parsed_correctly(self, setup):
        pipe, spider, _, repo = setup
        pipe.run(spider)
        assert repo.recent_yields("listings", 5) == [4]

    def test_archives_the_json_response(self, setup, tmp_snapshots):
        from pathlib import Path

        pipe, spider, _, _ = setup
        result = pipe.run(spider)
        files = list((Path(tmp_snapshots) / "listings" / result.run_id).glob("*.html"))
        assert files
        assert '"reference"' in files[0].read_text()

    def test_token_expiring_between_pages_does_not_fail_the_run(self, setup):
        pipe, spider, server, _ = setup
        pipe.run(spider)

        server.state.expire_all()

        second = pipe.run(spider)
        assert second.ok
        assert second.seen == 4
        assert server.state.refresh_calls == 1

    def test_losing_access_entirely_surfaces_rather_than_silently_emptying(self, setup):
        from scraper_pipeline.auth import AuthError

        pipe, spider, server, _ = setup
        pipe.run(spider)

        # Credentials revoked between runs.
        server.state.expire_all()
        server.state.reject_refresh = True
        pipe._fetcher.auth.password = "revoked"

        # Must raise, not quietly record an empty run. An auth failure that
        # looks like "the source had no data" is how silent data loss starts.
        with pytest.raises(AuthError):
            pipe.run(spider)


class TestUnauthenticatedFallback:
    def test_no_credentials_means_no_auth_strategy(self, monkeypatch):
        from scraper_pipeline.auth import NoAuth

        monkeypatch.delenv("LISTINGS_USERNAME", raising=False)
        assert isinstance(ListingsSpider().auth(), NoAuth)

    def test_html_on_a_json_endpoint_yields_nothing(self):
        spider = ListingsSpider()
        items = list(spider.parse("http://x/listings", "<html><body>Login</body></html>"))
        assert items == []

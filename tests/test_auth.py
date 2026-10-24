"""Authenticated scraping, against a real HTTP server.

Not mocked. These tests start a JWT-protected server, so what is exercised is
the actual request and response cycle including headers, status codes and
expiry, rather than an assumption about how httpx behaves.
"""

from __future__ import annotations

import pytest

from scraper_pipeline.auth import AuthError, BearerTokenAuth, CookieAuth, NoAuth
from scraper_pipeline.spiders.base import Fetcher
from tests.authserver import VALID_PASSWORD, VALID_USER, AuthServer


@pytest.fixture()
def server():
    with AuthServer() as s:
        yield s


def bearer(server, *, user=VALID_USER, password=VALID_PASSWORD, session_file=None, refresh=True):
    return BearerTokenAuth(
        login_url=f"{server.base_url}/auth/login",
        refresh_url=f"{server.base_url}/auth/refresh" if refresh else None,
        username=user,
        password=password,
        session_file=session_file,
    )


class TestLogin:
    def test_authenticated_fetch_succeeds(self, server):
        f = Fetcher("listings", auth=bearer(server))
        resp = f.get(f"{server.base_url}/listings")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 4
        f.close()

    def test_without_auth_the_same_url_is_401(self, server):
        import httpx

        f = Fetcher("listings", auth=NoAuth())
        with pytest.raises(httpx.HTTPStatusError) as exc:
            f.get(f"{server.base_url}/listings")
        assert exc.value.response.status_code == 401
        f.close()

    def test_bad_credentials_raise_rather_than_retry(self, server):
        # Repeated login attempts are what trigger lockouts, so a rejected
        # credential must fail immediately and not go through the retry path.
        f = Fetcher("listings", auth=bearer(server, password="wrong"))
        with pytest.raises(AuthError, match="login rejected with 401"):
            f.get(f"{server.base_url}/listings")
        assert server.state.login_calls == 1
        f.close()

    def test_login_happens_once_across_many_pages(self, server):
        # The failure this guards: re-authenticating per request.
        f = Fetcher("listings", auth=bearer(server))
        for _ in range(5):
            f.get(f"{server.base_url}/listings")
        assert server.state.login_calls == 1
        assert server.state.protected_calls == 5
        f.close()

    def test_constructing_a_fetcher_does_no_network_io(self, server):
        Fetcher("listings", auth=bearer(server))
        assert server.state.login_calls == 0


class TestExpiryAndRefresh:
    def test_token_expiring_mid_run_is_recovered(self, server):
        f = Fetcher("listings", auth=bearer(server))
        assert f.get(f"{server.base_url}/listings").status_code == 200

        server.state.expire_all()  # token dies between pages

        assert f.get(f"{server.base_url}/listings").status_code == 200
        assert server.state.refresh_calls == 1
        f.close()

    def test_falls_back_to_a_full_login_when_refresh_is_rejected(self, server):
        f = Fetcher("listings", auth=bearer(server))
        f.get(f"{server.base_url}/listings")

        server.state.expire_all()
        server.state.reject_refresh = True

        assert f.get(f"{server.base_url}/listings").status_code == 200
        assert server.state.refresh_calls == 1
        assert server.state.login_calls == 2  # re-logged in
        f.close()

    def test_gives_up_when_credentials_stop_working_entirely(self, server):
        f = Fetcher("listings", auth=bearer(server))
        f.get(f"{server.base_url}/listings")

        # Credentials revoked mid-run: refresh fails and so does re-login.
        server.state.expire_all()
        server.state.reject_refresh = True
        f.auth.password = "revoked"

        with pytest.raises(AuthError):
            f.get(f"{server.base_url}/listings")
        f.close()

    def test_refresh_is_attempted_once_not_in_a_loop(self, server):
        f = Fetcher("listings", auth=bearer(server))
        f.get(f"{server.base_url}/listings")
        server.state.expire_all()
        server.state.reject_refresh = True
        f.auth.password = "revoked"

        with pytest.raises(AuthError):
            f.get(f"{server.base_url}/listings")
        # One refresh attempt and one re-login attempt. Not a storm.
        assert server.state.refresh_calls == 1
        assert server.state.login_calls == 2
        f.close()

    def test_near_expiry_token_is_renewed_before_being_used(self, server):
        server.state.token_lifetime = 30  # inside the 60s leeway
        f = Fetcher("listings", auth=bearer(server))
        f.get(f"{server.base_url}/listings")
        f.get(f"{server.base_url}/listings")
        # Renewed pre-emptively rather than after a wasted 401 round trip.
        assert server.state.refresh_calls >= 1
        f.close()


class TestSessionPersistence:
    def test_session_survives_a_new_fetcher(self, server, tmp_path):
        path = tmp_path / "session.json"

        first = Fetcher("listings", auth=bearer(server, session_file=path))
        first.get(f"{server.base_url}/listings")
        first.close()
        assert path.exists()

        second = Fetcher("listings", auth=bearer(server, session_file=path))
        second.get(f"{server.base_url}/listings")
        second.close()

        # Second process reused the stored token instead of logging in again.
        assert server.state.login_calls == 1

    def test_session_file_is_not_world_readable(self, server, tmp_path):
        path = tmp_path / "session.json"
        f = Fetcher("listings", auth=bearer(server, session_file=path))
        f.get(f"{server.base_url}/listings")
        f.close()
        assert oct(path.stat().st_mode)[-3:] == "600"

    def test_a_corrupt_session_file_falls_back_to_login(self, server, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("not json at all")
        f = Fetcher("listings", auth=bearer(server, session_file=path))
        assert f.get(f"{server.base_url}/listings").status_code == 200
        f.close()


class TestCredentialHandling:
    def test_token_is_never_written_to_logs(self, server, capsys):
        f = Fetcher("listings", auth=bearer(server))
        f.get(f"{server.base_url}/listings")
        token = f.auth._token
        output = capsys.readouterr()
        assert token
        assert token not in output.out
        assert token not in output.err
        f.close()

    def test_from_env_refuses_to_run_without_credentials(self, server, monkeypatch):
        monkeypatch.delenv("LISTINGS_USERNAME", raising=False)
        monkeypatch.delenv("LISTINGS_PASSWORD", raising=False)
        with pytest.raises(AuthError, match="must be set"):
            BearerTokenAuth.from_env("LISTINGS", f"{server.base_url}/auth/login")

    def test_from_env_reads_credentials(self, server, monkeypatch):
        monkeypatch.setenv("LISTINGS_USERNAME", VALID_USER)
        monkeypatch.setenv("LISTINGS_PASSWORD", VALID_PASSWORD)
        auth = BearerTokenAuth.from_env("LISTINGS", f"{server.base_url}/auth/login")
        assert auth.username == VALID_USER


def cookie_auth(server):
    return CookieAuth(
        login_url=f"{server.base_url}/auth/form-login",
        username=VALID_USER,
        password=VALID_PASSWORD,
        username_field="email",
        success_cookie="sessionid",
    )


class TestCookieAuth:
    def test_form_login_sets_the_session_cookie(self, server):
        f = Fetcher("listings", auth=cookie_auth(server))
        f._ensure_authenticated()
        assert "sessionid" in f._client.cookies
        f.close()

    def test_rejects_a_200_that_set_no_session_cookie(self, server):
        # A login page re-rendered with an error returns 200. Treating that as
        # success is a classic and easy mistake, and it is why checking the
        # status code alone is not enough.
        server.state.form_sets_cookie = False
        f = Fetcher("listings", auth=cookie_auth(server))
        with pytest.raises(AuthError, match="set no 'sessionid' cookie"):
            f.get(f"{server.base_url}/listings")
        f.close()

    def test_bad_credentials_still_raise(self, server):
        auth = cookie_auth(server)
        auth.password = "wrong"
        f = Fetcher("listings", auth=auth)
        with pytest.raises(AuthError, match="login rejected with 401"):
            f.get(f"{server.base_url}/listings")
        f.close()

"""Authenticated sources.

Most data worth scraping sits behind a login, so this is not an edge case. The
parts that actually matter in production are unglamorous:

1. **Log in once, not per request.** A scraper that re-authenticates on every
   page is the fastest way to get an account flagged.
2. **Refresh on 401, retry once, then give up.** Tokens expire mid-run. The
   request that discovers it should recover rather than fail the run, but it
   must not retry forever against a server that is rejecting the credentials.
3. **Persist the session across runs.** Re-logging in every time the process
   starts is both slow and conspicuous.
4. **Never log the token.** It is a credential, and scraper logs get pasted
   into tickets.

The strategy object is deliberately separate from the fetcher so that a source
using cookie login and a source using a bearer token share everything else.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger()


class AuthError(RuntimeError):
    """Authentication failed in a way retrying will not fix."""


class AuthStrategy(ABC):
    """How a source proves who we are."""

    @abstractmethod
    def authenticate(self, client: httpx.Client) -> None:
        """Acquire credentials and apply them to the client."""

    @abstractmethod
    def refresh(self, client: httpx.Client) -> bool:
        """Renew after a 401. Return False when renewal is not possible."""

    def is_expired(self) -> bool:
        return False


class NoAuth(AuthStrategy):
    """Default. Public sources need nothing."""

    def authenticate(self, client: httpx.Client) -> None:
        return None

    def refresh(self, client: httpx.Client) -> bool:
        return False


class BearerTokenAuth(AuthStrategy):
    """Username and password in, bearer token out.

    Covers most JSON APIs, including NestJS services issuing JWTs.
    """

    def __init__(
        self,
        login_url: str,
        username: str,
        password: str,
        *,
        username_field: str = "email",
        password_field: str = "password",
        token_field: str = "access_token",
        refresh_field: str | None = "refresh_token",
        refresh_url: str | None = None,
        session_file: str | Path | None = None,
        leeway_seconds: int = 60,
    ) -> None:
        self.login_url = login_url
        self.username = username
        self.password = password
        self.username_field = username_field
        self.password_field = password_field
        self.token_field = token_field
        self.refresh_field = refresh_field
        self.refresh_url = refresh_url
        self.session_file = Path(session_file) if session_file else None
        self.leeway = leeway_seconds

        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float | None = None

    @classmethod
    def from_env(cls, prefix: str, login_url: str, **kwargs) -> BearerTokenAuth:
        """Build from environment variables.

        Credentials belong in the environment or a secret store, never in the
        spider definition, which is a file that gets committed.
        """
        user = os.getenv(f"{prefix}_USERNAME")
        password = os.getenv(f"{prefix}_PASSWORD")
        if not user or not password:
            raise AuthError(f"{prefix}_USERNAME and {prefix}_PASSWORD must be set")
        return cls(login_url, user, password, **kwargs)

    # Session persistence ----------------------------------------------------

    def _save(self) -> None:
        if not self.session_file or not self._token:
            return
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps(
                {
                    "token": self._token,
                    "refresh_token": self._refresh_token,
                    "expires_at": self._expires_at,
                }
            ),
            encoding="utf-8",
        )
        # The file holds a live credential.
        self.session_file.chmod(0o600)

    def _load(self) -> bool:
        if not self.session_file or not self.session_file.exists():
            return False
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        self._token = data.get("token")
        self._refresh_token = data.get("refresh_token")
        self._expires_at = data.get("expires_at")
        return bool(self._token) and not self.is_expired()

    def is_expired(self) -> bool:
        if self._expires_at is None:
            return False
        return time.time() >= (self._expires_at - self.leeway)

    # Core flow --------------------------------------------------------------

    def _apply(self, client: httpx.Client) -> None:
        client.headers["Authorization"] = f"Bearer {self._token}"

    def _consume(self, payload: dict) -> None:
        token = payload.get(self.token_field)
        if not token:
            raise AuthError(f"login response has no '{self.token_field}' field")
        self._token = token
        if self.refresh_field:
            self._refresh_token = payload.get(self.refresh_field) or self._refresh_token
        expires_in = payload.get("expires_in")
        self._expires_at = time.time() + float(expires_in) if expires_in else None

    def authenticate(self, client: httpx.Client) -> None:
        # Reuse a stored session rather than logging in again.
        if self._load():
            self._apply(client)
            log.info("auth_session_reused", url=self.login_url)
            return

        try:
            resp = client.post(
                self.login_url,
                json={
                    self.username_field: self.username,
                    self.password_field: self.password,
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"login request failed: {exc}") from exc

        if resp.status_code in (400, 401, 403):
            # Wrong credentials. Retrying cannot help and repeated attempts
            # are what trigger account lockouts.
            raise AuthError(f"login rejected with {resp.status_code}")
        resp.raise_for_status()

        self._consume(resp.json())
        self._apply(client)
        self._save()
        # Deliberately no token in the log line.
        log.info("auth_login_ok", url=self.login_url, expires_at=self._expires_at)

    def refresh(self, client: httpx.Client) -> bool:
        if self.refresh_url and self._refresh_token:
            try:
                resp = client.post(
                    self.refresh_url, json={"refresh_token": self._refresh_token}
                )
            except httpx.HTTPError as exc:
                log.warning("auth_refresh_failed", error=str(exc))
                return False
            if resp.status_code == 200:
                self._consume(resp.json())
                self._apply(client)
                self._save()
                log.info("auth_refreshed")
                return True
            log.warning("auth_refresh_rejected", status=resp.status_code)

        # No refresh token, or the refresh was rejected: fall back to a full
        # login. Clear the stored session first so a stale file is not reloaded.
        self._token = None
        self._expires_at = None
        if self.session_file and self.session_file.exists():
            self.session_file.unlink()
        try:
            self.authenticate(client)
            return True
        except AuthError as exc:
            log.error("auth_reauth_failed", error=str(exc))
            return False


class CookieAuth(AuthStrategy):
    """Form login that sets a session cookie.

    The httpx client holds the cookie jar, so once the POST succeeds there is
    nothing to attach per request.
    """

    def __init__(
        self,
        login_url: str,
        username: str,
        password: str,
        *,
        username_field: str = "username",
        password_field: str = "password",
        extra_fields: dict[str, str] | None = None,
        success_cookie: str | None = None,
    ) -> None:
        self.login_url = login_url
        self.username = username
        self.password = password
        self.username_field = username_field
        self.password_field = password_field
        self.extra_fields = extra_fields or {}
        self.success_cookie = success_cookie

    def authenticate(self, client: httpx.Client) -> None:
        data = {
            self.username_field: self.username,
            self.password_field: self.password,
            **self.extra_fields,
        }
        try:
            resp = client.post(self.login_url, data=data)
        except httpx.HTTPError as exc:
            raise AuthError(f"login request failed: {exc}") from exc

        if resp.status_code in (400, 401, 403):
            raise AuthError(f"login rejected with {resp.status_code}")
        resp.raise_for_status()

        if self.success_cookie and self.success_cookie not in client.cookies:
            # A 200 that did not set the session cookie usually means the login
            # page was re-rendered with an error rather than the login failing
            # loudly, which is a common and easy thing to miss.
            raise AuthError(
                f"login returned {resp.status_code} but set no '{self.success_cookie}' cookie"
            )
        log.info("auth_cookie_login_ok", url=self.login_url)

    def refresh(self, client: httpx.Client) -> bool:
        client.cookies.clear()
        try:
            self.authenticate(client)
            return True
        except AuthError as exc:
            log.error("auth_reauth_failed", error=str(exc))
            return False

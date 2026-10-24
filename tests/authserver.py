"""A small JWT-protected server to test authenticated scraping against.

Shipped with the tests rather than pointed at a real service, because the cases
worth testing are the ones you can only trigger on a server you control: a token
that expires mid-run, a refresh token that is rejected, credentials revoked
between pages. You cannot arrange any of that against someone else's API.

The route shape mirrors a NestJS service (`/auth/login`, `/auth/refresh`,
`Bearer` tokens, 401 on expiry), so the spider written against this works
unchanged against a real one.

Pure stdlib: no extra dependency to install in CI.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

VALID_USER = "scraper@example.com"
VALID_PASSWORD = "correct-horse"

LISTINGS = [
    {"reference": "LAG-0001", "title": "2 bedroom flat, Lekki Phase 1",
     "price": 2_500_000, "currency": "NGN", "bedrooms": 2, "listing_type": "rent"},
    {"reference": "LAG-0002", "title": "3 bedroom terrace, Ikoyi",
     "price": 7_200_000, "currency": "NGN", "bedrooms": 3, "listing_type": "rent"},
    {"reference": "LAG-0003", "title": "Studio apartment, Yaba",
     "price": 900_000, "currency": "NGN", "bedrooms": 1, "listing_type": "rent"},
    {"reference": "ABV-0004", "title": "4 bedroom duplex, Maitama",
     "price": 180_000_000, "currency": "NGN", "bedrooms": 4, "listing_type": "sale"},
]


class State:
    """Server state the tests manipulate to force each failure mode."""

    def __init__(self) -> None:
        self.tokens: dict[str, float] = {}       # token -> expiry
        self.refresh_tokens: set[str] = set()
        self.token_lifetime = 3600.0
        self.reject_refresh = False              # force refresh to fail
        self.form_sets_cookie = True             # form login issues a session cookie
        self.login_calls = 0
        self.refresh_calls = 0
        self.protected_calls = 0

    def issue(self) -> dict:
        token = uuid.uuid4().hex
        refresh = uuid.uuid4().hex
        self.tokens[token] = time.time() + self.token_lifetime
        self.refresh_tokens.add(refresh)
        return {
            "access_token": token,
            "refresh_token": refresh,
            "expires_in": int(self.token_lifetime),
        }

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self.tokens.get(token)
        return expiry is not None and time.time() < expiry

    def expire_all(self) -> None:
        """Simulate every issued token expiring mid-run."""
        for token in self.tokens:
            self.tokens[token] = time.time() - 1


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *args) -> None:  # keep test output clean
        return

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def _bearer(self) -> str | None:
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else None

    def do_POST(self) -> None:
        if self.path == "/auth/login":
            self.state.login_calls += 1
            body = self._body()
            if body.get("email") != VALID_USER or body.get("password") != VALID_PASSWORD:
                self._send(401, {"message": "Invalid credentials"})
                return
            self._send(201, self.state.issue())
            return

        if self.path == "/auth/form-login":
            # Form-encoded login that sets a session cookie, as a server-rendered
            # app would. With form_sets_cookie off it returns 200 and sets
            # nothing, which is how a login page re-rendered with an error
            # actually behaves and the case most scrapers mistake for success.
            self.state.login_calls += 1
            body = self._form()
            if body.get("email") != VALID_USER or body.get("password") != VALID_PASSWORD:
                self._send(401, {"message": "Invalid credentials"})
                return
            if self.state.form_sets_cookie:
                sid = uuid.uuid4().hex
                self.state.tokens[sid] = time.time() + self.state.token_lifetime
                body_bytes = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"sessionid={sid}; Path=/")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
            self._send(200, {"ok": False, "error": "re-rendered login page"})
            return

        if self.path == "/auth/refresh":
            self.state.refresh_calls += 1
            token = self._body().get("refresh_token")
            if self.state.reject_refresh or token not in self.state.refresh_tokens:
                self._send(401, {"message": "Refresh token rejected"})
                return
            self._send(200, self.state.issue())
            return

        self._send(404, {"message": "Not found"})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return

        if self.path.startswith("/listings"):
            self.state.protected_calls += 1
            if not self.state.valid(self._bearer()):
                self._send(401, {"message": "Unauthorized"})
                return
            self._send(200, {"data": LISTINGS, "total": len(LISTINGS)})
            return

        self._send(404, {"message": "Not found"})


class AuthServer:
    """Context manager that runs the server on a free port."""

    def __init__(self) -> None:
        self.state = State()
        handler = type("BoundHandler", (Handler,), {"state": self.state})
        # 127.0.0.1 explicitly, not 0.0.0.0 or ::. Binding the wildcard causes
        # intermittent connection failures on macOS when the client resolves
        # localhost to IPv6 and the listener is on IPv4.
        self._httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> AuthServer:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

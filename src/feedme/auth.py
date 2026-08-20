"""OAuth 2.1 PKCE (S256) login flow, credential storage, and re-auth.

Endpoints, token shape, and the 401-handling pattern below are verified
against targeted fetches of the Swiggy Builders Club docs (see
CLAUDE.md §8): the token response has no ``refresh_token`` field ("not
wired in v1.0"), so ``reauthenticate()`` always re-runs the full
interactive login rather than attempting a refresh grant.

``client_id`` (obtained via Dynamic Client Registration, per the docs)
is the one remaining unverified piece — isolated as a single constant
below.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import typer
from rich import print as rprint

from models import Credentials

AUTHORIZE_URL = "https://mcp.swiggy.com/auth/authorize"
TOKEN_URL = "https://mcp.swiggy.com/auth/token"

CALLBACK_HOST = "localhost"
CALLBACK_PORT = 3000
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

# UNVERIFIED: real client_id requires Dynamic Client Registration, not
# yet documented in detail.
CLIENT_ID = "feedme-cli"

SCOPE = "mcp:tools"
CREDENTIALS_PATH = Path.home() / ".config" / "feedme" / "credentials.json"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorization_url(code_challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": SCOPE,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


class _CallbackResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.event = threading.Event()


def _make_callback_handler(result: _CallbackResult) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query)
            result.code = query.get("code", [None])[0]
            result.state = query.get("state", [None])[0]
            result.error = query.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>feedme: you can close this tab.</body></html>")
            result.event.set()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # silence default request logging

    return Handler


def _run_callback_server(timeout: float = 300.0) -> tuple[str, str]:
    """Blocks until the OAuth redirect hits localhost:3000/callback."""
    result = _CallbackResult()
    handler_cls = _make_callback_handler(result)
    server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), handler_cls)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    completed = result.event.wait(timeout=timeout)
    server.server_close()

    if not completed:
        raise TimeoutError("Timed out waiting for OAuth callback")
    if result.error:
        raise RuntimeError(f"OAuth authorization failed: {result.error}")
    if not result.code or not result.state:
        raise RuntimeError("OAuth callback missing code/state")
    return result.code, result.state


async def exchange_code_for_token(code: str, code_verifier: str) -> Credentials:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, json=payload)
        resp.raise_for_status()
        return Credentials.model_validate(resp.json())


def load_credentials() -> Credentials | None:
    if not CREDENTIALS_PATH.exists():
        return None
    return Credentials.model_validate_json(CREDENTIALS_PATH.read_text())


def save_credentials(creds: Credentials) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    CREDENTIALS_PATH.write_text(creds.model_dump_json())
    CREDENTIALS_PATH.chmod(0o600)


def is_token_expired(creds: Credentials) -> bool:
    return creds.is_expired()


async def login() -> Credentials:
    """Full interactive PKCE flow: opens a real browser for the one-time
    desktop OAuth step (not a "phone" flow — doesn't touch the
    zero-phone checkout constraint)."""
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorization_url(code_challenge, state)

    result_holder: dict[str, tuple[str, str]] = {}
    error_holder: dict[str, BaseException] = {}

    def _wait_for_callback() -> None:
        try:
            result_holder["value"] = _run_callback_server()
        except BaseException as exc:  # noqa: BLE001
            error_holder["value"] = exc

    server_thread = threading.Thread(target=_wait_for_callback, daemon=True)
    server_thread.start()

    webbrowser.open(auth_url)
    rprint(f"[cyan]Opening browser for login. If it didn't open, visit:[/]\n{auth_url}")

    server_thread.join()
    if "value" in error_holder:
        raise error_holder["value"]

    code, returned_state = result_holder["value"]
    if returned_state != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF, aborting")

    creds = await exchange_code_for_token(code, code_verifier)
    save_credentials(creds)
    return creds


async def reauthenticate() -> Credentials:
    """Called on HTTP 401 from mcp_client.py. No refresh_token exists in
    v1.0, so this always re-runs the full interactive login."""
    return await login()


auth_app = typer.Typer(name="auth", help="feedme authentication commands")


@auth_app.callback()
def _auth_callback() -> None:
    """feedme authentication commands.

    A Typer app with exactly one command and no callback collapses into
    that single command directly (so `python -m feedme.auth login` would
    fail with "unexpected extra argument (login)"). This no-op callback
    keeps `login` a required, explicitly-named subcommand, matching the
    documented `python -m feedme.auth login` usage.
    """


@auth_app.command("login")
def login_cmd() -> None:
    creds = asyncio.run(login())
    rprint(f"[green]Logged in.[/] Token expires at {creds.expires_at:%Y-%m-%d %H:%M} UTC.")


if __name__ == "__main__":
    auth_app()

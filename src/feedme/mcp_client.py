"""Async HTTP client for Swiggy's MCP servers.

Verified live against the real server (2026-08-20): this is a standard
MCP server using the Streamable HTTP transport — JSON-RPC 2.0 envelope,
``method: "tools/call"``, and it requires
``Accept: application/json, text/event-stream`` or it responds
``406 Not Acceptable``. This replaced an earlier unverified REST-style
guess.

Verified against docs (CLAUDE.md §8): 401 -> reauthenticate -> retry
once; rate limits surface as 429 with a ``Retry-After`` header; error
classification prefers the ``error.message`` string over status/JSON-RPC
codes as the primary signal.
"""

from __future__ import annotations

import itertools
import json
from types import TracebackType
from typing import Any, Literal

import httpx

from feedme import auth
from models import Credentials

BASE_URL = "https://mcp.swiggy.com"
ServerName = Literal["food", "im", "dineout"]
SERVER_PATHS: dict[ServerName, str] = {"food": "/food", "im": "/im", "dineout": "/dineout"}

_request_ids = itertools.count(1)


class MCPError(Exception):
    """Base error for all MCP client failures."""


class MCPHTTPError(MCPError):
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        self.classification = _classify_error_message(body if isinstance(body, str) else str(body))
        super().__init__(f"MCP request failed with HTTP {status_code}: {body!r}")


class MCPAuthError(MCPError):
    """Raised when a request still fails after a reauthenticate+retry."""


class MCPRateLimitError(MCPError):
    def __init__(self, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(f"MCP rate limited; retry after {retry_after}s")


class MCPProtocolError(MCPError):
    """A JSON-RPC-level error returned inside a 200 response body."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        self.classification = _classify_error_message(message)
        super().__init__(f"MCP tool call failed (jsonrpc code {code}): {message}")


class MCPToolError(MCPError):
    """A domain-level tool failure: JSON-RPC succeeds (HTTP 200, no
    top-level "error") but the CallToolResult itself carries
    ``isError: true``. Confirmed live: e.g. search_menu returns this
    shape (rather than a JSON-RPC error) when a required argument is
    missing."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.classification = _classify_error_message(message)
        super().__init__(f"MCP tool reported an error: {message}")


def structured_content(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the machine-readable payload from a CallToolResult.
    Confirmed live: tool responses are shaped
    {"content": [...human-readable text...], "structuredContent": {...}}."""
    content: dict[str, Any] = result.get("structuredContent", {})
    return content


def _tool_error_text(result: dict[str, Any]) -> str:
    for block in result.get("content", []):
        if block.get("type") == "text":
            text: str = block["text"]
            return text
    return "tool reported isError=true with no text content"


def _classify_error_message(message: str) -> str:
    """Best-effort classification per the documented error.message
    prefixes (no symbolic error.code registry ships yet)."""
    if message.startswith("Invalid") or message.startswith("Missing"):
        return "bad_input"
    if "timeout" in message.lower():
        return "upstream_timeout"
    return "unknown"


class MCPClient:
    def __init__(self, server: ServerName, credentials: Credentials | None = None) -> None:
        self.server = server
        self._credentials = credentials if credentials is not None else auth.load_credentials()
        # base_url has no sub-path: joining "https://host" with a relative
        # "/food" path is unambiguous. Joining a base that already has a
        # sub-path (e.g. "https://host/food") with another leading-slash
        # relative path replaces the whole path per RFC 3986 and silently
        # drops "/food" — so the server path is folded into _build_request
        # instead of into base_url.
        self._client = httpx.AsyncClient(base_url=BASE_URL)

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Streamable HTTP transport requires accepting both content types
        # or the server responds 406 Not Acceptable (confirmed live).
        headers = {"Accept": "application/json, text/event-stream"}
        if self._credentials is not None:
            headers["Authorization"] = (
                f"{self._credentials.token_type} {self._credentials.access_token}"
            )
        return headers

    def _build_request(self, tool_name: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """JSON-RPC 2.0 tools/call envelope, confirmed live against the
        real server."""
        payload = {
            "jsonrpc": "2.0",
            "id": next(_request_ids),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": kwargs},
        }
        return SERVER_PATHS[self.server], payload

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        resp = await self._client.post(path, json=payload, headers=self._headers())
        if resp.status_code == 429:
            retry_after_header = resp.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header else None
            raise MCPRateLimitError(retry_after)
        resp.raise_for_status()
        return resp

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any]:
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            envelope = _parse_sse_json(resp.text)
        else:
            envelope = resp.json()
        if "error" in envelope:
            error = envelope["error"]
            raise MCPProtocolError(error.get("code", 0), error.get("message", str(error)))
        result: dict[str, Any] = envelope.get("result", envelope)
        if result.get("isError"):
            raise MCPToolError(_tool_error_text(result))
        return result

    async def call_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        path, payload = self._build_request(tool_name, kwargs)
        try:
            resp = await self._post(path, payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._credentials = await auth.reauthenticate()
                try:
                    resp = await self._post(path, payload)
                except httpx.HTTPStatusError as retry_exc:
                    if retry_exc.response.status_code == 401:
                        raise MCPAuthError(
                            "reauthenticated but request still returned 401"
                        ) from retry_exc
                    raise MCPHTTPError(
                        retry_exc.response.status_code, retry_exc.response.text
                    ) from retry_exc
            else:
                raise MCPHTTPError(exc.response.status_code, exc.response.text) from exc
        return self._parse_response(resp)


def _parse_sse_json(text: str) -> dict[str, Any]:
    """Extract the JSON payload from an SSE-framed response (Streamable
    HTTP transport may return `text/event-stream` even for a single
    request/response exchange)."""
    for line in text.splitlines():
        if line.startswith("data:"):
            data: dict[str, Any] = json.loads(line[len("data:") :].strip())
            return data
    raise MCPError(f"No 'data:' line found in SSE response: {text!r}")

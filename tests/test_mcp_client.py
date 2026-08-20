from __future__ import annotations

import json

import httpx
import pytest
import respx

from feedme import mcp_client
from feedme.mcp_client import (
    MCPAuthError,
    MCPClient,
    MCPHTTPError,
    MCPRateLimitError,
    MCPToolError,
    structured_content,
)
from models import Credentials


@pytest.fixture
def creds() -> Credentials:
    return Credentials(access_token="tok-1")


@respx.mock
async def test_call_tool_success_unwraps_jsonrpc_envelope(creds):
    route = respx.post("https://mcp.swiggy.com/food").mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"ok": True}}}
        )
    )
    async with MCPClient("food", credentials=creds) as client:
        result = await client.call_tool("get_addresses")
    assert structured_content(result) == {"ok": True}
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-1"
    assert route.calls.last.request.headers["accept"] == "application/json, text/event-stream"


@respx.mock
async def test_call_tool_sends_jsonrpc_tools_call_envelope(creds):
    route = respx.post("https://mcp.swiggy.com/food").mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    )
    async with MCPClient("food", credentials=creds) as client:
        await client.call_tool("search_menu", query="pizza", addressId="a1")

    payload = json.loads(route.calls.last.request.content)
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tools/call"
    assert payload["params"] == {
        "name": "search_menu",
        "arguments": {"query": "pizza", "addressId": "a1"},
    }


@respx.mock
async def test_call_tool_is_error_raises_mcp_tool_error(creds):
    respx.post("https://mcp.swiggy.com/food").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "addressId is required"}],
                    "structuredContent": {},
                },
            },
        )
    )
    async with MCPClient("food", credentials=creds) as client:
        with pytest.raises(MCPToolError, match="addressId is required"):
            await client.call_tool("search_menu", query="pizza")


@respx.mock
async def test_call_tool_401_then_reauth_then_success(creds, monkeypatch):
    route = respx.post("https://mcp.swiggy.com/food").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": True})]
    )

    new_creds = Credentials(access_token="tok-2")

    async def fake_reauthenticate():
        return new_creds

    monkeypatch.setattr(mcp_client.auth, "reauthenticate", fake_reauthenticate)

    async with MCPClient("food", credentials=creds) as client:
        result = await client.call_tool("get_addresses")

    assert result == {"ok": True}
    assert route.call_count == 2
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-2"


@respx.mock
async def test_call_tool_401_persists_raises_auth_error(creds, monkeypatch):
    respx.post("https://mcp.swiggy.com/food").mock(
        side_effect=[httpx.Response(401), httpx.Response(401)]
    )

    async def fake_reauthenticate():
        return Credentials(access_token="tok-2")

    monkeypatch.setattr(mcp_client.auth, "reauthenticate", fake_reauthenticate)

    async with MCPClient("food", credentials=creds) as client:
        with pytest.raises(MCPAuthError):
            await client.call_tool("get_addresses")


@respx.mock
async def test_call_tool_non_401_error_raises_http_error(creds):
    respx.post("https://mcp.swiggy.com/food").mock(
        return_value=httpx.Response(500, text="Internal server error")
    )
    async with MCPClient("food", credentials=creds) as client:
        with pytest.raises(MCPHTTPError) as exc_info:
            await client.call_tool("get_addresses")
    assert exc_info.value.status_code == 500


@respx.mock
async def test_call_tool_rate_limited_raises_with_retry_after(creds):
    respx.post("https://mcp.swiggy.com/food").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "23"})
    )
    async with MCPClient("food", credentials=creds) as client:
        with pytest.raises(MCPRateLimitError) as exc_info:
            await client.call_tool("get_addresses")
    assert exc_info.value.retry_after == 23.0

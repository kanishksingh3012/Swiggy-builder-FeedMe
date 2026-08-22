"""Order tracking: polls track_food_order and renders progress with rich.

Confirmed live (2026-08-20): both tools take addressId/orderId
(camelCase), and get_food_orders requires addressId the same way
get_food_cart does. track_food_order was only testable against an
already-delivered order (no active order on the account), returning
{"orders": [], "statusMessage": "No tracking information..."} — so the
per-order tracking-entry shape for an actually *active* order is still
unverified. TERMINAL_STATUSES values are an unverified guess pending
that.
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.status import Status

from feedme.mcp_client import MCPClient, structured_content
from models import Order, TrackingStatus

TERMINAL_STATUSES = {"DELIVERED", "CANCELLED", "FAILED"}

# UNVERIFIED: real stage/status strings for an active order have never
# been observed (no in-flight order to test against — see module
# docstring). This is a best-effort, Swiggy-app-style phrasing map with
# a humanized fallback for anything unrecognized, so the terminal line
# degrades gracefully rather than showing a raw enum either way.
_STAGE_PHRASES: dict[str, str] = {
    "ORDER_PLACED": "Order placed",
    "PLACED": "Order placed",
    "CONFIRMED": "Restaurant confirmed your order",
    "ACCEPTED": "Restaurant confirmed your order",
    "PREPARING": "Preparing your food",
    "FOOD_PREPARING": "Preparing your food",
    "OUT_FOR_DELIVERY": "Driver is on the way",
    "DISPATCHED": "Driver is on the way",
    "REACHING_RESTAURANT": "Driver is reaching the restaurant",
    "ARRIVING_AT_RESTAURANT": "Driver is reaching the restaurant",
    "REACHED_RESTAURANT": "Driver reached the restaurant",
    "PICKED_UP": "Driver picked up your order",
    "NEARBY": "Driver is reaching your location",
    "REACHING_YOU": "Driver is reaching your location",
    "DELIVERED": "Delivered",
    "CANCELLED": "Order cancelled",
    "FAILED": "Order failed",
}


def _friendly_stage(raw: str | None) -> str:
    if not raw:
        return "Waiting for an update"
    key = raw.strip().upper().replace(" ", "_")
    return _STAGE_PHRASES.get(key, raw.replace("_", " ").strip().capitalize())


def _status_line(status: TrackingStatus) -> str:
    """Single-line, Swiggy-app-style status text, e.g.
    '12 min left — Driver is reaching the restaurant'."""
    stage_text = _friendly_stage(status.stage or status.status)
    if status.eta_minutes is not None:
        eta = int(status.eta_minutes)
        return f"{eta} min left — {stage_text}"
    if status.status_message:
        return status.status_message
    return stage_text


async def get_delivery_status(client: MCPClient, order_id: str) -> TrackingStatus:
    result = await client.call_tool("track_food_order", orderId=order_id)
    content = structured_content(result)
    orders = content.get("orders", [])
    if orders:
        return TrackingStatus.model_validate({"order_id": order_id, **orders[0]})
    return TrackingStatus(order_id=order_id, status_message=content.get("statusMessage"))


async def get_order_details_text(client: MCPClient, order_id: str) -> str:
    """get_food_order_details — a real tool (referenced by
    get_food_orders' own output) that wasn't in the doc-derived tool
    list at all. Confirmed live (2026-08-21): unlike every other tool
    tested, its structuredContent is always empty — the order details
    (items, address, total, payment method) only exist as human-
    readable text in `content`. No model built around it since there's
    no structure to model; this just surfaces that text as-is."""
    result = await client.call_tool("get_food_order_details", orderId=order_id)
    for block in result.get("content", []):
        if block.get("type") == "text":
            text: str = block["text"]
            return text
    return ""


async def list_orders(client: MCPClient, address_id: str) -> list[Order]:
    result = await client.call_tool("get_food_orders", addressId=address_id)
    orders = structured_content(result).get("orders", [])
    return [Order.model_validate(o) for o in orders]


def _is_terminal(status: TrackingStatus) -> bool:
    value = (status.status or status.stage or "").upper()
    return value in TERMINAL_STATUSES


TRACK_ORDER_DEFAULT_TIMEOUT = 1800.0


def _final_message(status: TrackingStatus, *, timed_out: bool) -> tuple[str, str]:
    """(message, rich style) for the line printed once tracking stops."""
    value = (status.status or status.stage or "").upper()
    if value == "DELIVERED":
        return "✅ Order delivered!", "bold green"
    if value == "CANCELLED":
        return "❌ Order cancelled.", "bold red"
    if value == "FAILED":
        return "❌ Order failed.", "bold red"
    if timed_out:
        minutes = int(TRACK_ORDER_DEFAULT_TIMEOUT // 60)
        return (
            f"⏱ Stopped tracking after {minutes} min without a final status — "
            "check the Swiggy app.",
            "yellow",
        )
    return "Stopped tracking.", "yellow"


async def track_order(
    client: MCPClient,
    order_id: str,
    *,
    poll_interval: float = 5.0,
    timeout: float = TRACK_ORDER_DEFAULT_TIMEOUT,
    render: bool = True,
) -> TrackingStatus:
    deadline = time.monotonic() + timeout
    status_widget = Status("Tracking order...") if render else None
    if status_widget is not None:
        status_widget.start()
    timed_out = False
    try:
        while True:
            status = await get_delivery_status(client, order_id)
            if status_widget is not None:
                status_widget.update(_status_line(status))
            if _is_terminal(status):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(poll_interval)
    finally:
        if status_widget is not None:
            status_widget.stop()

    if render:
        message, style = _final_message(status, timed_out=timed_out)
        Console().print(f"[{style}]{message}[/]")
    return status

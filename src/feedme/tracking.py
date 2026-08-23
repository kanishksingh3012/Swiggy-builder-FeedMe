"""Order tracking: polls track_food_order and renders progress with rich.

Confirmed live (2026-08-20): both tools take addressId/orderId
(camelCase), and get_food_orders requires addressId the same way
get_food_cart does.
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.status import Status

from feedme.mcp_client import MCPClient, structured_content
from models import Order, TrackingStatus

TERMINAL_STATUSES = {"DELIVERED", "CANCELLED", "FAILED"}


def _status_line(status: TrackingStatus) -> str:
    """Single-line status text. Confirmed live 2026-08-23: `title` is
    already Swiggy's own human-readable text ("Preparing your order"),
    and `etaText` is pre-formatted ("29 mins") — no stage-to-phrase
    mapping needed, unlike the original unverified guess this
    replaces."""
    if status.title:
        if status.eta_text:
            return f"{status.title} — {status.eta_text} left"
        return status.title
    if status.status_message:
        return status.status_message
    return "Waiting for an update"


async def get_delivery_status(client: MCPClient, order_id: str) -> TrackingStatus:
    result = await client.call_tool("track_food_order", orderId=order_id)
    content = structured_content(result)
    orders = content.get("orders", [])
    if orders:
        return TrackingStatus.model_validate({"order_id": order_id, **orders[0]})
    return TrackingStatus.model_validate(
        {"order_id": order_id, "status_message": content.get("statusMessage")}
    )


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


def _is_active(status: TrackingStatus) -> bool:
    """Confirmed live 2026-08-23: track_food_order stops returning an
    entry at all once an order concludes — there's no terminal status
    value to look for inside one. Absence of both `title` and `status`
    is that "no entry" case. `status` is still checked defensively in
    case a terminal value (delivered/cancelled/failed) is ever seen on
    a final entry rather than the entry just disappearing."""
    if status.title is None and status.status is None:
        return False
    return (status.status or "").upper() not in TERMINAL_STATUSES


async def _final_status(
    client: MCPClient, address_id: str, order_id: str, fallback: TrackingStatus
) -> TrackingStatus:
    """track_food_order goes empty once an order concludes, with no
    final status value of its own — get_food_orders is the only place
    that still reports it (confirmed live 2026-08-23: its `status`
    field reads "Delivered" for a finished order, same field/alias as
    TrackingStatus.status)."""
    for order in await list_orders(client, address_id):
        if order.order_id == order_id:
            return TrackingStatus.model_validate(
                {
                    "order_id": order_id,
                    "status": order.status,
                    "status_message": fallback.status_message,
                }
            )
    return fallback


TRACK_ORDER_DEFAULT_TIMEOUT = 1800.0


def _final_message(status: TrackingStatus, *, timed_out: bool) -> tuple[str, str]:
    """(message, rich style) for the line printed once tracking stops."""
    value = (status.status or "").upper()
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
    address_id: str,
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
            if not _is_active(status):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(poll_interval)
    finally:
        if status_widget is not None:
            status_widget.stop()

    # track_food_order's own entry disappearing (no title/status at
    # all) carries no final status by itself — look it up via
    # get_food_orders rather than reporting a blank outcome.
    if not timed_out and status.title is None and status.status is None:
        status = await _final_status(client, address_id, order_id, fallback=status)

    if render:
        message, style = _final_message(status, timed_out=timed_out)
        Console().print(f"[{style}]{message}[/]")
    return status

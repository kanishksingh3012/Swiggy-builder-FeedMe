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

from rich.status import Status

from feedme.mcp_client import MCPClient, structured_content
from models import Order, TrackingStatus

TERMINAL_STATUSES = {"DELIVERED", "CANCELLED", "FAILED"}


async def get_delivery_status(client: MCPClient, order_id: str) -> TrackingStatus:
    result = await client.call_tool("track_food_order", orderId=order_id)
    content = structured_content(result)
    orders = content.get("orders", [])
    if orders:
        return TrackingStatus.model_validate({"order_id": order_id, **orders[0]})
    return TrackingStatus(order_id=order_id, status_message=content.get("statusMessage"))


async def list_orders(client: MCPClient, address_id: str) -> list[Order]:
    result = await client.call_tool("get_food_orders", addressId=address_id)
    orders = structured_content(result).get("orders", [])
    return [Order.model_validate(o) for o in orders]


def _is_terminal(status: TrackingStatus) -> bool:
    value = (status.status or status.stage or "").upper()
    return value in TERMINAL_STATUSES


async def track_order(
    client: MCPClient,
    order_id: str,
    *,
    poll_interval: float = 5.0,
    timeout: float = 1800.0,
    render: bool = True,
) -> TrackingStatus:
    deadline = time.monotonic() + timeout
    status_widget = Status("Tracking order...") if render else None
    if status_widget is not None:
        status_widget.start()
    try:
        while True:
            status = await get_delivery_status(client, order_id)
            if status_widget is not None:
                status_widget.update(f"Order {order_id}: {status.stage or status.status or '...'}")
            if _is_terminal(status) or time.monotonic() >= deadline:
                return status
            await asyncio.sleep(poll_interval)
    finally:
        if status_widget is not None:
            status_widget.stop()

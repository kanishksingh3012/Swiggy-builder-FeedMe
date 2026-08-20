"""Zero-phone payment gate and order placement.

Confirmed live (2026-08-20): get_payment_options takes no arguments and
operates on the current server-side cart; its structuredContent key is
`allMethods` (not `payment_options` — that was a guess, now fixed).
Individual payment-method object shape is still UNVERIFIED — the live
account had an empty cart, so `allMethods` came back `[]`. Matching is
alias-based and case-insensitive so it's not brittle to minor naming
drift once real entries are seen.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from feedme.mcp_client import MCPClient, structured_content
from models import Cart, Order, PaymentOption

ZERO_PHONE_ALIASES: dict[str, set[str]] = {
    "swiggy_money": {"swiggy_money", "swiggymoney", "wallet", "prepaid_wallet"},
    "cod": {"cod", "cash_on_delivery", "cashondelivery"},
}
_ALL_ZERO_PHONE_TOKENS = {t for tokens in ZERO_PHONE_ALIASES.values() for t in tokens}


class ZeroPhonePaymentUnavailable(Exception):
    pass


def _is_zero_phone(option: PaymentOption) -> bool:
    candidates = {option.method_type, option.method_id, option.display_name}
    normalized = {c.strip().lower().replace(" ", "_") for c in candidates if c}
    return bool(normalized & _ALL_ZERO_PHONE_TOKENS)


async def get_payment_options(client: MCPClient) -> list[PaymentOption]:
    result = await client.call_tool("get_payment_options")
    methods = structured_content(result).get("allMethods", [])
    return [PaymentOption.model_validate(o) for o in methods]


async def get_zero_phone_payment_options(client: MCPClient) -> list[PaymentOption]:
    options = await get_payment_options(client)
    filtered = [o for o in options if _is_zero_phone(o)]
    if not filtered:
        raise ZeroPhonePaymentUnavailable(
            "Only phone-based payment options (UPI/QR) are available. "
            "feedme will not trigger a mobile handoff — top up Swiggy Money "
            "or enable COD to proceed."
        )
    return filtered


def _render_receipt(order: Order) -> None:
    table = Table(title="Order placed")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Order ID", order.order_id)
    table.add_row("Status", order.status or "-")
    table.add_row("Total", f"{order.total_amount}" if order.total_amount is not None else "-")
    table.add_row("Payment method", order.payment_method or "-")
    Console().print(table)


async def checkout(client: MCPClient, cart: Cart, payment_option: PaymentOption) -> Order:
    # Carts are addressed by addressId, not a separate cart_id (confirmed
    # live — see cart.py). paymentMethodId's casing follows the same
    # confirmed pattern but hasn't been independently tested —
    # place_food_order is a real, money-moving action, deliberately not
    # smoke-tested live.
    result = await client.call_tool(
        "place_food_order", addressId=cart.address_id, paymentMethodId=payment_option.method_id
    )
    order = Order.model_validate(structured_content(result))
    _render_receipt(order)
    return order

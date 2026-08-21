"""Payment gate and order placement.

Confirmed live (2026-08-20/21): get_payment_options takes no arguments
and operates on the current server-side cart; its structuredContent key
is `allMethods`. Real entries seen: 6 UPI app intents (gpay://, etc — id
contains "://", requires opening a mobile app and authorizing there),
one QR option (id exactly "PayWithQR", groupName "UPI" — same as the
app intents at the group level, but distinguished by id), and COD. No
Swiggy Money entry has been seen on the test account.

Policy (2026-08-21, deliberate project decision, not a default): full
mobile-app UPI handoffs (gpay://, phonepe://, etc.) are always refused
— that's the non-negotiable "zero-phone" core. QR is different: it's
still explicitly offered as an opt-in choice alongside COD/Swiggy Money
rather than ever being silently preferred, because unlike an app
handoff it's a single scan-and-pay, not a full app authorization flow.
This is a real, intentional narrowing of the original "no QR, ever"
claim — see README for the current framing.
"""

from __future__ import annotations

from typing import Any

import qrcode
from rich.console import Console
from rich.table import Table

from feedme.mcp_client import MCPClient, structured_content
from models import Cart, Order, PaymentOption

ZERO_PHONE_ALIASES: dict[str, set[str]] = {
    "swiggy_money": {"swiggy_money", "swiggymoney", "wallet", "prepaid_wallet"},
    "cod": {"cod", "cash_on_delivery", "cashondelivery"},
}
_ALL_ZERO_PHONE_TOKENS = {t for tokens in ZERO_PHONE_ALIASES.values() for t in tokens}

QR_ALIASES = {"qr", "paywithqr", "pay_with_qr", "scan_and_pay", "scanandpay"}

# Real, likely-looking keys for a QR payload in place_food_order's
# response — UNVERIFIED, since generating one means actually calling
# place_food_order, which has never been done live. First real QR order
# will confirm/correct this list.
_LIKELY_QR_PAYLOAD_KEYS = (
    "qrCode",
    "qrCodeUrl",
    "qrCodeBase64",
    "qrImageUrl",
    "paymentLink",
    "upiLink",
    "upiIntentUrl",
    "intentUrl",
)


class ZeroPhonePaymentUnavailable(Exception):
    pass


class NoUsablePaymentOptions(Exception):
    pass


def _is_zero_phone(option: PaymentOption) -> bool:
    candidates = {option.method_type, option.method_id, option.display_name}
    normalized = {c.strip().lower().replace(" ", "_") for c in candidates if c}
    return bool(normalized & _ALL_ZERO_PHONE_TOKENS)


def _is_qr(option: PaymentOption) -> bool:
    candidates = {option.method_type, option.method_id, option.display_name}
    normalized = {c.strip().lower().replace(" ", "_") for c in candidates if c}
    return bool(normalized & QR_ALIASES)


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


async def get_available_payment_options(client: MCPClient) -> list[PaymentOption]:
    """Zero-phone options plus QR, combined — the actual set feedme will
    let you pick from. QR is included but never preferred (see module
    docstring); full UPI app intents are still always excluded. Raises
    only if literally nothing but app intents remain."""
    options = await get_payment_options(client)
    combined = [o for o in options if _is_zero_phone(o) or _is_qr(o)]
    if not combined:
        raise NoUsablePaymentOptions(
            "Only full mobile-app UPI handoffs are available (no COD, "
            "Swiggy Money, or QR option). feedme refuses to trigger a "
            "mobile app authorization."
        )
    return combined


def _render_receipt(order: Order) -> None:
    table = Table(title="Order placed")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Order ID", order.order_id)
    table.add_row("Status", order.status or "-")
    table.add_row("Total", f"{order.total_amount}" if order.total_amount is not None else "-")
    table.add_row("Payment method", order.payment_method or "-")
    Console().print(table)


def find_qr_payload(data: dict[str, Any]) -> str | None:
    """Best-effort search for a QR/payment-link string in a
    place_food_order response — UNVERIFIED, since generating one means
    actually calling place_food_order, which has never been done live.
    Checks the known likely key names first, then does a shallow search
    through nested dicts for any of them, since the real field could be
    nested under `data` the way other responses have been."""
    for key in _LIKELY_QR_PAYLOAD_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    for value in data.values():
        if isinstance(value, dict):
            found = find_qr_payload(value)
            if found:
                return found
    return None


def _render_qr(payload: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    Console().print(f"[dim]If the QR doesn't render clearly, here's the raw payload:[/]\n{payload}")


async def checkout(client: MCPClient, cart: Cart, payment_option: PaymentOption) -> Order:
    # Carts are addressed by addressId, not a separate cart_id (confirmed
    # live — see cart.py). paymentMethodId was wrong — a real live run
    # (2026-08-21) failed with "No payment method selected... call
    # place_food_order with the selected paymentMethod", which both
    # names the real argument (paymentMethod, not paymentMethodId) and
    # confirms the value is the option's id directly. No order was
    # placed by that failed attempt.
    result = await client.call_tool(
        "place_food_order", addressId=cart.address_id, paymentMethod=payment_option.method_id
    )
    content = structured_content(result)
    order = Order.model_validate(content)
    _render_receipt(order)

    if _is_qr(payment_option):
        qr_payload = find_qr_payload(content)
        if qr_payload:
            Console().print("[cyan]Scan to pay:[/]")
            _render_qr(qr_payload)
        else:
            Console().print(
                "[yellow]QR payment selected, but no QR/payment-link field was found in "
                "the response under any expected key. The order may still be pending "
                "payment — check the Swiggy app.[/]"
            )

    return order

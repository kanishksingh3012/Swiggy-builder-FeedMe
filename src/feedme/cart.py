"""Cart flushing/updating and coupon maximization.

Confirmed live (2026-08-20), including full add-item / flush / coupon
round-trips against a real cart (item added, verified via get_food_cart,
then flushed back to empty — no order was placed):
  - Carts are addressed by addressId, not a separate cart_id (the
    `cart_id` that does exist lives nested inside the response's
    `data`, keyed per-request — not something callers need to track).
  - update_food_cart requires addressId, restaurantId, and cartItems
    (not "items"); each entry needs a `menu_item_id` key (not "itemId"
    — confirmed by trial: "itemId"/"id" both got rejected as
    INVALID_ITEM_IDS_IN_REQUEST, menu_item_id succeeded).
  - fetch_food_coupons requires addressId and restaurantId, and only
    returns coupons when there's an active cart with items in it.

apply_food_coupon confirmed live too (2026-08-21): addressId + couponCode
(the coupon's `title` text, e.g. "SPECIALS" — not its `id` UUID) applied
a real ₹130 discount against a ₹299 cart, then the cart was flushed back
to empty. The real fetch_food_coupons response also carries a genuine
per-coupon `applicable` boolean the server precomputes — best_coupon()
ranks off that (plus a best-effort parse of "Save ₹N" subtitle text)
rather than the nonexistent discount_value/min_order_value fields.
"""

from __future__ import annotations

import re
from typing import Any

from feedme.mcp_client import MCPClient, MCPToolError, structured_content
from models import AddonGroup, Cart, Coupon, MenuItem

_SAVE_AMOUNT_RE = re.compile(r"(?:save|maximum discount)[:\s]*₹\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


async def flush_cart(client: MCPClient, address_id: str) -> None:
    await client.call_tool("flush_food_cart", addressId=address_id)


class CartUpdateFailed(Exception):
    """Raised when update_food_cart reports successful: false. Caught
    live (2026-08-21): adding "Classic Chicken Roll" failed with
    errorCodes ["INVALID_ADDON"] — it requires an addon/variant
    selection (size, spice level, etc.) that feedme has no UI for yet.
    Before this fix, add_items() ignored the response entirely and
    called get_cart() regardless, so the cart silently stayed unchanged
    and the real failure only resurfaced several steps later as a
    baffling "no payment options available" error — the actual point of
    failure was here, just never surfaced."""

    def __init__(self, message: str, error_codes: list[str]) -> None:
        self.error_codes = error_codes
        super().__init__(message)


class MandatoryAddonRequired(Exception):
    """Raised when a cart item carries at least one addon group with
    min_addons >= 1 — a required choice (size, base, spice level, etc.)
    that has to be picked before the item can be correctly ordered.

    Confirmed live (2026-08-23): unlike CartUpdateFailed's INVALID_ADDON
    case (where update_food_cart rejects outright with no item detail
    at all), some restaurants' items report `successful` absent/true and
    still echo their mandatory groups via items[].valid_addons — the
    add nominally "succeeds" without the required choice actually being
    captured. Checked before the CartUpdateFailed path so the mandatory
    groups get surfaced whenever they're available, regardless of the
    response's own success signal, since a silently-defaulted mandatory
    choice is exactly as unsafe to proceed on as an outright rejection.

    Submitting a chosen addon back to update_food_cart is a *separate*,
    still-unsolved problem (live-tested 2026-08-23: 9 different request
    shapes for the addons field were all rejected identically) — this
    exception only carries the groups for *display*, not for retrying
    the add with a selection."""

    def __init__(self, item_name: str, groups: list[AddonGroup]) -> None:
        self.item_name = item_name
        self.groups = groups
        names = ", ".join(g.group_name or g.group_id for g in groups)
        super().__init__(f"{item_name} requires a choice: {names}")


def _mandatory_addons(content: dict[str, Any]) -> tuple[str, list[AddonGroup]] | None:
    data = content.get("data") or {}
    for item in data.get("items", []):
        groups = [AddonGroup.model_validate(g) for g in item.get("valid_addons") or []]
        mandatory = [g for g in groups if g.mandatory]
        if mandatory:
            return item.get("name") or "This item", mandatory
    return None


async def add_items(client: MCPClient, address_id: str, items: list[MenuItem]) -> Cart:
    restaurant_ids = {i.restaurant_id for i in items if i.restaurant_id}
    if len(restaurant_ids) > 1:
        raise ValueError(
            f"Cannot add items from multiple restaurants in one cart: {restaurant_ids}"
        )
    restaurant_id = next(iter(restaurant_ids), None)
    cart_items = [{"menu_item_id": i.item_id, "quantity": 1} for i in items]
    result = await client.call_tool(
        "update_food_cart", addressId=address_id, restaurantId=restaurant_id, cartItems=cart_items
    )
    content = structured_content(result)
    mandatory = _mandatory_addons(content)
    if mandatory is not None:
        item_name, groups = mandatory
        raise MandatoryAddonRequired(item_name, groups)
    if content.get("successful") is False:
        default = "Cart update failed"
        message = content.get("titleMessage") or content.get("statusMessage") or default
        raise CartUpdateFailed(message, content.get("errorCodes") or [])
    # update_food_cart's own response has no addressId field either
    # (confirmed live, same gap as apply_food_coupon — see
    # apply_best_coupon's docstring for the real bug this caused). Not
    # trusting it as a complete Cart; get_food_cart reliably is.
    return await get_cart(client, address_id)


async def get_cart(client: MCPClient, address_id: str) -> Cart:
    result = await client.call_tool("get_food_cart", addressId=address_id)
    return Cart.model_validate(structured_content(result))


async def fetch_coupons(client: MCPClient, address_id: str, restaurant_id: str) -> list[Coupon]:
    result = await client.call_tool(
        "fetch_food_coupons", addressId=address_id, restaurantId=restaurant_id
    )
    content = structured_content(result)
    coupons = [
        c for section in content.get("coupon_sections", []) for c in section.get("coupons", [])
    ]
    return [Coupon.model_validate(c) for c in coupons]


def _effective_discount(coupon: Coupon, cart_total: float) -> float:
    """Numeric ranking score. Prefers a real discount_value if some
    future/differently-shaped response supplies one; otherwise parses
    the "Save ₹N" / "Maximum discount: ₹N" pattern out of the real
    subtitle/description text. Falls back to 0 (still rankable, just
    lowest priority) rather than excluding the coupon outright — an
    `applicable: true` coupon with unparseable text is still a valid
    pick, just not a differentiable one."""
    if coupon.discount_value is not None:
        if coupon.discount_type == "percentage":
            discount = cart_total * (coupon.discount_value / 100)
            if coupon.max_discount is not None:
                discount = min(discount, coupon.max_discount)
            return discount
        return coupon.discount_value
    for text in (coupon.subtitle, coupon.description):
        if text:
            match = _SAVE_AMOUNT_RE.search(text)
            if match:
                return float(match.group(1))
    return 0.0


def best_coupon(coupons: list[Coupon], cart_total: float) -> Coupon | None:
    """Ranks by the server's own `applicable` flag first (confirmed
    real and authoritative — see module docstring), then by parsed
    savings amount. min_order_value is checked too, only as a fallback
    for coupons where `applicable` wasn't supplied at all."""
    eligible = [
        c
        for c in coupons
        if c.applicable is True
        or (c.applicable is None and (c.min_order_value is None or cart_total >= c.min_order_value))
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda c: _effective_discount(c, cart_total))


async def apply_best_coupon(client: MCPClient, cart: Cart, restaurant_id: str) -> Cart:
    """Confirmed live (2026-08-21): the server's `applicable: true` flag
    is necessary but not sufficient — a coupon can still be rejected at
    the point of application for item-level reasons it doesn't capture
    (e.g. "Not applicable on pre-packaged & combo items", seen on a real
    call). Checkout shouldn't fail just because a discount didn't land,
    so an application-time rejection here is swallowed and the cart is
    returned as-is rather than raised — a missed coupon is a poor
    outcome, a crashed order is a worse one.

    A real bug lived here until 2026-08-21: on success this used to
    build the returned Cart straight from apply_food_coupon's own
    response — which has no `addressId` field at all (confirmed live).
    That silently dropped Cart.address_id to None, which then made
    place_food_order fail for real with "addressId is required" (caught
    by an actual live run). Fixed by re-fetching the cart via
    get_food_cart after a successful apply, since that response is
    confirmed to reliably include addressId — not trusting each
    mutation endpoint's own response shape to be a complete Cart."""
    if cart.address_id is None:
        return cart
    coupons = await fetch_coupons(client, cart.address_id, restaurant_id)
    chosen = best_coupon(coupons, cart.subtotal or 0.0)
    if chosen is None:
        return cart
    try:
        await client.call_tool(
            "apply_food_coupon", addressId=cart.address_id, couponCode=chosen.coupon_code
        )
    except MCPToolError:
        return cart
    return await get_cart(client, cart.address_id)

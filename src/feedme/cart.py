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

apply_food_coupon's argument casing is NOT live-tested: its terms
explicitly state "Coupon code can be applied only once in 2 hr on this
restaurant" — a real, if non-monetary, side effect not worth burning on
a guess. See best_coupon()'s docstring for why "maximize the discount"
doesn't actually work against the real API today.
"""

from __future__ import annotations

from feedme.mcp_client import MCPClient, structured_content
from models import Cart, Coupon, MenuItem


async def flush_cart(client: MCPClient, address_id: str) -> None:
    await client.call_tool("flush_food_cart", addressId=address_id)


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
    return Cart.model_validate(structured_content(result))


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
    """Best-effort numeric ranking — only meaningful if discount_value
    is populated, which the real fetch_food_coupons response never does
    (see Coupon's docstring). Kept so this still works if a future/
    differently-shaped response does supply numbers."""
    if coupon.discount_value is None:
        return 0.0
    if coupon.discount_type == "percentage":
        discount = cart_total * (coupon.discount_value / 100)
        if coupon.max_discount is not None:
            discount = min(discount, coupon.max_discount)
        return discount
    return coupon.discount_value


def best_coupon(coupons: list[Coupon], cart_total: float) -> Coupon | None:
    """Against the real Swiggy coupon shape (free-text terms, no
    discount_value/min_order_value fields), this always returns None —
    there's nothing numeric to rank. That's a deliberate, safe
    degradation: guessing which coupon is "best" from unstructured text
    risks applying the wrong one and burning its real 2-hour
    per-restaurant cooldown for no reason. Ranking real coupons would
    need either parsing the free-text descriptions or a still-unverified
    per-coupon applicability field — not attempted here."""
    eligible = [c for c in coupons if c.min_order_value is None or cart_total >= c.min_order_value]
    ranked = [c for c in eligible if c.discount_value is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda c: _effective_discount(c, cart_total))


async def apply_best_coupon(client: MCPClient, cart: Cart, restaurant_id: str) -> Cart:
    if cart.address_id is None:
        return cart
    coupons = await fetch_coupons(client, cart.address_id, restaurant_id)
    chosen = best_coupon(coupons, cart.subtotal or 0.0)
    if chosen is None:
        return cart
    # UNVERIFIED argument casing — see module docstring on the 2hr-cooldown risk.
    result = await client.call_tool(
        "apply_food_coupon", addressId=cart.address_id, couponCode=chosen.coupon_code
    )
    return Cart.model_validate(structured_content(result))

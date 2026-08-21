from __future__ import annotations

import pytest

from feedme.cart import add_items, apply_best_coupon, best_coupon
from feedme.mcp_client import MCPToolError
from models import Cart, Coupon, MenuItem


def test_best_coupon_flat_discount():
    coupons = [
        Coupon(coupon_code="FLAT50", discount_type="flat", discount_value=50, min_order_value=200),
        Coupon(coupon_code="FLAT20", discount_type="flat", discount_value=20, min_order_value=0),
    ]
    result = best_coupon(coupons, cart_total=300)
    assert result is not None
    assert result.coupon_code == "FLAT50"


def test_best_coupon_percentage_with_cap():
    coupons = [
        Coupon(
            coupon_code="PCT10",
            discount_type="percentage",
            discount_value=10,
            max_discount=30,
            min_order_value=0,
        ),
        Coupon(coupon_code="FLAT25", discount_type="flat", discount_value=25, min_order_value=0),
    ]
    # 10% of 1000 = 100, capped at 30 -> PCT10 gives 30, FLAT25 gives 25
    result = best_coupon(coupons, cart_total=1000)
    assert result is not None
    assert result.coupon_code == "PCT10"


def test_best_coupon_min_order_value_gating():
    coupons = [
        Coupon(coupon_code="BIG", discount_type="flat", discount_value=100, min_order_value=1000),
        Coupon(coupon_code="SMALL", discount_type="flat", discount_value=10, min_order_value=0),
    ]
    result = best_coupon(coupons, cart_total=300)
    assert result is not None
    assert result.coupon_code == "SMALL"


def test_best_coupon_empty_list_returns_none():
    assert best_coupon([], cart_total=300) is None


def test_best_coupon_no_eligible_coupon_returns_none():
    coupons = [
        Coupon(coupon_code="BIG", discount_type="flat", discount_value=100, min_order_value=1000)
    ]
    assert best_coupon(coupons, cart_total=300) is None


def test_best_coupon_real_shape_ranks_by_applicable_and_parsed_savings():
    # Confirmed live 2026-08-21: real fetch_food_coupons responses have
    # no discount_value/min_order_value, but DO have a real `applicable`
    # boolean the server precomputes, plus free-text like "Save ₹130 on
    # this order!" for ones that qualify. SPECIALS was actually applied
    # live against a ₹299 cart for a confirmed real ₹130 discount.
    coupons = [
        Coupon(title="SWIGGYIT", subtitle="Save ₹100 on this order!", applicable=True),
        Coupon(title="SPECIALS", subtitle="Save ₹130 on this order!", applicable=True),
        Coupon(title="FLAT200", subtitle="Add ₹999 more to avail this offer", applicable=False),
    ]
    result = best_coupon(coupons, cart_total=299)
    assert result is not None
    assert result.coupon_code == "SPECIALS"


def test_best_coupon_excludes_non_applicable():
    coupons = [Coupon(title="FLAT200", subtitle="Add ₹999 more", applicable=False)]
    assert best_coupon(coupons, cart_total=299) is None


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        return {"structuredContent": {"addressId": kwargs.get("addressId")}}


async def test_add_items_sends_menu_item_id_and_cart_items_key():
    # Confirmed live: update_food_cart needs "cartItems" (not "items")
    # with each entry keyed "menu_item_id" (not "itemId"/"id" — those
    # were rejected as INVALID_ITEM_IDS_IN_REQUEST).
    client = _FakeClient()
    item = MenuItem(item_id="94940476", restaurant_id="558346")
    await add_items(client, "addr1", [item])
    tool_name, kwargs = client.calls[0]
    assert tool_name == "update_food_cart"
    assert kwargs["addressId"] == "addr1"
    assert kwargs["restaurantId"] == "558346"
    assert kwargs["cartItems"] == [{"menu_item_id": "94940476", "quantity": 1}]


class _RejectingClient:
    """Simulates the real, confirmed-live behavior where a coupon
    reports applicable=true but apply_food_coupon still rejects it for
    an item-level reason (e.g. "Not applicable on pre-packaged & combo
    items")."""

    async def call_tool(self, tool_name, **kwargs):
        if tool_name == "fetch_food_coupons":
            coupon = {"title": "SPECIALS", "subtitle": "Save ₹130", "applicable": True}
            return {"structuredContent": {"coupon_sections": [{"coupons": [coupon]}]}}
        if tool_name == "apply_food_coupon":
            raise MCPToolError("Not applicable on pre-packaged & combo items.")
        raise AssertionError(f"unexpected tool call: {tool_name}")


async def test_apply_best_coupon_swallows_application_time_rejection():
    client = _RejectingClient()
    cart = Cart(address_id="addr1", subtotal=299)
    result = await apply_best_coupon(client, cart, "restaurant1")
    assert result is cart


async def test_add_items_rejects_mixed_restaurants():
    client = _FakeClient()
    items = [
        MenuItem(item_id="1", restaurant_id="r1"),
        MenuItem(item_id="2", restaurant_id="r2"),
    ]
    with pytest.raises(ValueError, match="multiple restaurants"):
        await add_items(client, "addr1", items)

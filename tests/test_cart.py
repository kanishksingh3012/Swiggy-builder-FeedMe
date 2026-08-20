from __future__ import annotations

import pytest

from feedme.cart import add_items, best_coupon
from models import Coupon, MenuItem


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


def test_best_coupon_real_shape_has_no_discount_value_returns_none():
    # Confirmed live 2026-08-20: real fetch_food_coupons responses have
    # no discount_value/min_order_value at all (free-text terms only).
    # best_coupon must degrade to None rather than guess.
    coupons = [
        Coupon(title="SWIGGYIT", subtitle="Add ₹179 more to avail this offer"),
        Coupon(title="FLAT75", subtitle="Add ₹199 more to avail this offer"),
    ]
    assert best_coupon(coupons, cart_total=89) is None


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


async def test_add_items_rejects_mixed_restaurants():
    client = _FakeClient()
    items = [
        MenuItem(item_id="1", restaurant_id="r1"),
        MenuItem(item_id="2", restaurant_id="r2"),
    ]
    with pytest.raises(ValueError, match="multiple restaurants"):
        await add_items(client, "addr1", items)

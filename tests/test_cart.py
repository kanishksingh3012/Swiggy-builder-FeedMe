from __future__ import annotations

import pytest

from feedme.cart import (
    CartUpdateFailed,
    MandatoryAddonRequired,
    add_items,
    apply_best_coupon,
    best_coupon,
)
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


class _RealisticClient:
    """Confirmed live 2026-08-21: update_food_cart's and
    apply_food_coupon's own success responses have NO addressId field
    at all — only get_food_cart reliably includes it. A real run hit
    this: place_food_order failed with "addressId is required" because
    the Cart returned from a successful coupon application had silently
    lost its address_id. This fake reproduces that exact response gap
    so add_items()/apply_best_coupon() are proven to route around it via
    get_food_cart rather than trusting the mutation response."""

    async def call_tool(self, tool_name, **kwargs):
        if tool_name == "update_food_cart":
            return {"structuredContent": {"statusMessage": "CART_UPDATED_SUCCESSFULLY"}}
        if tool_name == "fetch_food_coupons":
            coupon = {"title": "SPECIALS", "subtitle": "Save ₹130", "applicable": True}
            return {"structuredContent": {"coupon_sections": [{"coupons": [coupon]}]}}
        if tool_name == "apply_food_coupon":
            return {"structuredContent": {"statusMessage": "CART_UPDATED_SUCCESSFULLY"}}
        if tool_name == "get_food_cart":
            return {"structuredContent": {"addressId": kwargs["addressId"], "subtotal": 299}}
        raise AssertionError(f"unexpected tool call: {tool_name}")


async def test_add_items_preserves_address_id_despite_response_gap():
    client = _RealisticClient()
    item = MenuItem(item_id="153131180", restaurant_id="968342")
    result = await add_items(client, "addr1", [item])
    assert result.address_id == "addr1"


async def test_apply_best_coupon_preserves_address_id_despite_response_gap():
    client = _RealisticClient()
    cart = Cart(address_id="addr1", subtotal=299)
    result = await apply_best_coupon(client, cart, "restaurant1")
    assert result.address_id == "addr1"


async def test_add_items_rejects_mixed_restaurants():
    client = _FakeClient()
    items = [
        MenuItem(item_id="1", restaurant_id="r1"),
        MenuItem(item_id="2", restaurant_id="r2"),
    ]
    with pytest.raises(ValueError, match="multiple restaurants"):
        await add_items(client, "addr1", items)


class _InvalidAddonClient:
    """Confirmed live 2026-08-21: adding "Classic Chicken Roll" got
    update_food_cart to return successful: false with errorCodes
    ["INVALID_ADDON"] instead of raising or returning an HTTP error — a
    200 JSON-RPC response that looks superficially fine unless the
    successful field is actually checked."""

    async def call_tool(self, tool_name, **kwargs):
        assert tool_name == "update_food_cart"
        return {
            "structuredContent": {
                "successful": False,
                "titleMessage": "Please select required options",
                "errorCodes": ["INVALID_ADDON"],
            }
        }


async def test_add_items_raises_on_unsuccessful_update():
    client = _InvalidAddonClient()
    item = MenuItem(item_id="i1", restaurant_id="r1")
    with pytest.raises(CartUpdateFailed) as excinfo:
        await add_items(client, "addr1", [item])
    assert excinfo.value.error_codes == ["INVALID_ADDON"]
    assert "required options" in str(excinfo.value)


class _MandatoryAddonClient:
    """Confirmed live 2026-08-23: some restaurants' items report the
    cart update as successful (or omit `successful` entirely) while
    still echoing a mandatory addon group (minAddons=1) under
    items[].valid_addons — e.g. Rolls Mania's "Base" choice. The add
    nominally goes through without the required choice ever being
    captured, which is exactly as unsafe to proceed on as an outright
    rejection."""

    async def call_tool(self, tool_name, **kwargs):
        assert tool_name == "update_food_cart"
        return {
            "structuredContent": {
                "statusCode": 0,
                "statusMessage": "CART_UPDATED_SUCCESSFULLY",
                "data": {
                    "items": [
                        {
                            "menu_item_id": 170952729,
                            "name": "Aloo Fry Roll",
                            "valid_addons": [
                                {
                                    "group_id": 311074758,
                                    "group_name": "Base",
                                    "minAddons": 1,
                                    "maxAddons": 1,
                                    "choices": [
                                        {"id": 32856461, "name": "Maida Base", "price": 0},
                                        {"id": 32856462, "name": "Wheat Base", "price": 1500},
                                    ],
                                },
                                {
                                    "group_id": 311074759,
                                    "group_name": "Upgrade your Roll",
                                    "minAddons": 0,
                                    "maxAddons": 3,
                                    "choices": [
                                        {
                                            "id": 32856463,
                                            "name": "Single Egg Omelette",
                                            "price": 2000,
                                        }
                                    ],
                                },
                            ],
                        }
                    ]
                },
            }
        }


async def test_add_items_raises_mandatory_addon_required_even_when_response_succeeds():
    client = _MandatoryAddonClient()
    item = MenuItem(item_id="170952729", restaurant_id="492395")
    with pytest.raises(MandatoryAddonRequired) as excinfo:
        await add_items(client, "addr1", [item])
    assert excinfo.value.item_name == "Aloo Fry Roll"
    # Only the mandatory (minAddons>=1) group surfaces — the optional
    # "Upgrade your Roll" upsell group is not something feedme should
    # ever block on.
    assert [g.group_name for g in excinfo.value.groups] == ["Base"]
    base = excinfo.value.groups[0]
    assert [c.name for c in base.choices] == ["Maida Base", "Wheat Base"]
    # price is paise on the wire (confirmed live) — 1500 paise is +₹15.
    assert base.choices[1].price == 15

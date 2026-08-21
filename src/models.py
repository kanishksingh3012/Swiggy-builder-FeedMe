"""Pydantic schemas for data moving through the feedme pipeline.

The exact JSON shapes returned by Swiggy's MCP tools are not fully
verified (see CLAUDE.md §8), so every model here is deliberately
permissive: known fields are typed, everything else is tolerated via
``extra="allow"`` and a ``raw`` passthrough, so an unexpected upstream
field never crashes the client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    model_config = ConfigDict(extra="allow")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 432000
    scope: str | None = None
    obtained_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def expires_at(self) -> datetime:
        return self.obtained_at + timedelta(seconds=self.expires_in)

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(seconds=skew_seconds)


class Address(BaseModel):
    """Verified live against get_addresses on 2026-08-20 — field names
    and nesting (structuredContent.addresses[]) confirmed against a real
    response, not a guess."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    address_line: str | None = Field(default=None, alias="addressLine")
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    address_category: str | None = Field(default=None, alias="addressCategory")
    address_tag: str | None = Field(default=None, alias="addressTag")
    raw: dict[str, Any] = Field(default_factory=dict)


class Restaurant(BaseModel):
    """Verified live against search_restaurants on 2026-08-21 — real
    fields are id/name/avgRating/deliveryTimeMinutes, nothing like the
    original restaurant_id/eta_minutes/rating guess."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    restaurant_id: str = Field(alias="id")
    name: str | None = None
    eta_minutes: float | None = Field(default=None, alias="deliveryTimeMinutes")
    rating: float | None = Field(default=None, alias="avgRating")
    cuisines: list[str] = Field(default_factory=list)
    cost_for_two: str | None = Field(default=None, alias="costForTwo")
    area_name: str | None = Field(default=None, alias="areaName")
    veg: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MenuItem(BaseModel):
    """Verified live against both search_menu and get_restaurant_menu on
    2026-08-20/21. The real server mixes camelCase (isVeg, imageUrl,
    hasAddons) and snake_case (menu_item_id, restaurant_id,
    restaurant_name) field names in the same object — not a typo here,
    that inconsistency is real. Worse: search_menu items key their id as
    "menu_item_id", but get_restaurant_menu items (nested under
    categories[].items, not a flat "items" list — see
    search.get_restaurant_menu) key the *same* id as plain "id" instead
    — hence AliasChoices trying both. eta_minutes/protein_g were never
    present in a real response; kept as optional guesses since the
    CLI's --fastest/protein filters need them if the server ever
    supplies them under some name."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    item_id: str = Field(validation_alias=AliasChoices("menu_item_id", "id"))
    name: str | None = None
    description: str | None = None
    price: float | None = None
    currency: str = "INR"
    eta_minutes: float | None = None
    protein_g: float | None = None
    restaurant_id: str | None = None
    restaurant_name: str | None = None
    rating: float | None = None
    in_stock: bool | None = Field(default=None, alias="inStock")
    veg: bool | None = Field(default=None, alias="isVeg")
    is_bestseller: bool | None = Field(default=None, alias="isBestseller")
    raw: dict[str, Any] = Field(default_factory=dict)


class CartLineItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    item_id: str
    quantity: int = 1
    price: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Cart(BaseModel):
    """Verified live against get_food_cart on 2026-08-20 for the *empty*
    cart case: there is no cart_id — carts are addressed by addressId
    (same id from get_addresses), not a separate cart identifier. The
    server wraps everything in a generic status envelope; `data` is
    presumably the actual cart contents when non-empty, but that shape
    is still unverified since the live cart was empty during testing.
    items/subtotal below are kept as a convenience view over `data` once
    its real shape is known — not yet populated from anything real."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    address_id: str | None = Field(default=None, alias="addressId")
    status_message: str | None = Field(default=None, alias="statusMessage")
    successful: bool | None = None
    data: dict[str, Any] | None = None
    items: list[CartLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Coupon(BaseModel):
    """Verified live against fetch_food_coupons on 2026-08-20, including
    a real apply_food_coupon call (title text like "SPECIALS" is the
    couponCode apply_food_coupon wants — confirmed, applied a real ₹130
    discount, then cleaned up). There's no numeric discount/min-order
    field, but there IS a real `applicable` boolean the server
    precomputes — cart.best_coupon() ranks off that plus a best-effort
    parse of the "Save ₹N" subtitle text, not off discount_value/
    max_discount/min_order_value below, which are kept only as a
    fallback for a hypothetical differently-shaped response and are
    never populated by the real API."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    coupon_code: str = Field(alias="title")
    id: str | None = None
    subtitle: str | None = None
    description: str | None = None
    ribbon_text: str | None = Field(default=None, alias="ribbonText")
    applicable: bool | None = None
    discount_type: str | None = None  # never populated by the real API — see docstring
    discount_value: float | None = None
    max_discount: float | None = None
    min_order_value: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PaymentOption(BaseModel):
    """Verified live against get_payment_options on 2026-08-21 (real
    cart, stopped short of place_food_order). Real entries in
    `allMethods` look like {"id": "gpay://upi/", "groupName": "UPI",
    "displayName": "Google Pay", "enabled": true, ...} — nothing like
    the guessed method_id/method_type/display_name shape. On this
    account there was no Swiggy Money entry at all; only UPI intents
    (mobile app handoff), a desktop QR option, and COD."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    method_id: str = Field(alias="id")
    display_name: str | None = Field(default=None, alias="displayName")
    method_type: str | None = Field(default=None, alias="groupName")
    enabled: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Order(BaseModel):
    """Verified live against get_food_orders on 2026-08-20 (past-order
    listing shape). order_total is a currency-formatted string
    ("₹242"), not a number — kept as a string rather than guessing at
    parsing. The shape of a *freshly placed* order from place_food_order
    itself is still unverified (never called live — real money)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    order_id: str = Field(alias="orderId")
    status: str | None = Field(default=None, alias="orderStatus")
    delivery_status: str | None = Field(default=None, alias="orderDeliveryStatus")
    order_total: str | None = Field(default=None, alias="orderTotal")
    total_amount: float | None = None
    restaurant_name: str | None = Field(default=None, alias="restaurantName")
    payment_method: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TrackingStatus(BaseModel):
    """track_food_order confirmed live to return {"orders": [...],
    "statusMessage": ...} — but only for the "nothing to track" case (no
    active order available to test against). Per-order tracking-entry
    shape inside `orders` is still unverified."""

    model_config = ConfigDict(extra="allow")

    order_id: str = ""
    stage: str | None = None
    status: str | None = None
    eta_minutes: float | None = None
    status_message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MCPResult(BaseModel):
    """Generic envelope for parsing an arbitrary tool response before a
    higher layer coerces it into one of the specific models above."""

    model_config = ConfigDict(extra="allow")

    raw: dict[str, Any] = Field(default_factory=dict)

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

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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
    rating: float | None = None  # per-dish rating, confirmed real — see docstring
    restaurant_rating: float | None = None  # filled in by search.discover(), not the raw response
    in_stock: bool | None = Field(default=None, alias="inStock")
    veg: bool | None = Field(default=None, alias="isVeg")
    is_bestseller: bool | None = Field(default=None, alias="isBestseller")
    raw: dict[str, Any] = Field(default_factory=dict)


class AddonChoice(BaseModel):
    """One selectable option within an AddonGroup. Confirmed live
    2026-08-23: `price` is in paise (hundredths of a rupee) — a real
    "Wheat Base" choice priced at 1500 is +₹15, not +₹1500 — so `price`
    below is the converted rupee value, with the raw paise figure kept
    in `raw`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    name: str | None = None
    price_paise: float = Field(default=0, alias="price")
    in_stock: bool | None = Field(default=None, alias="inStock")
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: object) -> object:
        # Confirmed live: group/choice ids come back as bare JSON
        # integers (e.g. 32856461), unlike menu item ids which are
        # already strings.
        return str(value) if value is not None else value

    @property
    def price(self) -> float:
        return self.price_paise / 100


class AddonGroup(BaseModel):
    """A group of choices for one menu item, as echoed back in
    update_food_cart's response under items[].valid_addons (confirmed
    live 2026-08-23 — not present in get_restaurant_menu's raw item
    listing, only discoverable via an actual cart-update call). A group
    with min_addons >= 1 is a *mandatory* choice — the item can't be
    correctly ordered without picking one, which is exactly the
    "Classic Chicken Roll" failure this models."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    group_id: str
    group_name: str | None = None
    choices: list[AddonChoice] = Field(default_factory=list)
    min_addons: int = Field(default=0, alias="minAddons")
    max_addons: int = Field(default=-1, alias="maxAddons")

    @field_validator("group_id", mode="before")
    @classmethod
    def _stringify_group_id(cls, value: object) -> object:
        return str(value) if value is not None else value

    @property
    def mandatory(self) -> bool:
        return self.min_addons >= 1


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
    """Confirmed live 2026-08-23 against a real in-flight order (every
    prior attempt only had a "nothing to track" empty response to go
    on) — the per-order entry is nothing like the original
    stage/eta_minutes guess. Real shape: {orderId, title, subtitle,
    etaText, orderStatus, progressPercentage, pollingDuration, icon,
    businessLine}. `title` is already Swiggy's own human-readable text
    ("Preparing your order") — no stage-name-to-phrase mapping needed
    anymore. `etaText` is a pre-formatted string ("29 mins"), not a
    number.

    Critically: once an order concludes, track_food_order stops
    returning an entry for it *at all* — {"orders": [], "statusMessage":
    "No tracking information found..."}, the exact same shape as "no
    active order". So there's no terminal status value to look for
    inside an entry; the entry's disappearance is itself the signal
    (see tracking._is_active / tracking.track_order, which falls back
    to get_food_orders for the real final status once that happens)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    order_id: str = Field(default="", alias="orderId")
    title: str | None = None
    subtitle: str | None = None
    eta_text: str | None = Field(default=None, alias="etaText")
    status: str | None = Field(default=None, alias="orderStatus")
    progress_percentage: str | None = Field(default=None, alias="progressPercentage")
    status_message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MCPResult(BaseModel):
    """Generic envelope for parsing an arbitrary tool response before a
    higher layer coerces it into one of the specific models above."""

    model_config = ConfigDict(extra="allow")

    raw: dict[str, Any] = Field(default_factory=dict)

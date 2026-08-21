"""Discovery: addresses -> menu/restaurant search -> filtering.

Argument casing confirmed live for all four tools below: addressId
(search_menu, search_restaurants, get_restaurant_menu — the latter also
needs restaurantId). get_restaurant_menu's real response shape is
notably different from search_menu's: items are nested under
categories[].items (not a flat "items" list), and each item's id key is
"id" rather than search_menu's "menu_item_id" — see models.MenuItem's
docstring for how that's reconciled.
"""

from __future__ import annotations

from feedme.mcp_client import MCPClient, structured_content
from models import Address, MenuItem, Restaurant


async def get_addresses(client: MCPClient) -> list[Address]:
    result = await client.call_tool("get_addresses")
    addresses = structured_content(result).get("addresses", [])
    return [Address.model_validate(a) for a in addresses]


async def search_menu(
    client: MCPClient, query: str, address_id: str | None = None
) -> list[MenuItem]:
    result = await client.call_tool("search_menu", query=query, addressId=address_id)
    items = structured_content(result).get("items", [])
    return [MenuItem.model_validate(i) for i in items]


async def search_restaurants(
    client: MCPClient, query: str, address_id: str | None = None
) -> list[Restaurant]:
    result = await client.call_tool("search_restaurants", query=query, addressId=address_id)
    restaurants = structured_content(result).get("restaurants", [])
    return [Restaurant.model_validate(r) for r in restaurants]


async def get_restaurant_menu(
    client: MCPClient, restaurant_id: str, address_id: str
) -> list[MenuItem]:
    result = await client.call_tool(
        "get_restaurant_menu", restaurantId=restaurant_id, addressId=address_id
    )
    categories = structured_content(result).get("categories", [])
    items = [i for category in categories for i in category.get("items", [])]
    return [MenuItem.model_validate(i) for i in items]


def filter_items(
    items: list[MenuItem],
    *,
    max_price: float | None = None,
    fastest: bool = False,
    min_protein: float | None = None,
) -> list[MenuItem]:
    filtered = items
    if max_price is not None:
        filtered = [i for i in filtered if i.price is None or i.price <= max_price]
    if min_protein is not None:
        filtered = [i for i in filtered if i.protein_g is not None and i.protein_g >= min_protein]
    if fastest:
        filtered = sorted(
            filtered, key=lambda i: i.eta_minutes if i.eta_minutes is not None else float("inf")
        )
    return filtered


async def discover(
    client: MCPClient,
    query: str,
    *,
    address_id: str | None = None,
    max_price: float | None = None,
    fastest: bool = False,
    min_protein: float | None = None,
) -> list[MenuItem]:
    items = await search_menu(client, query, address_id=address_id)

    # search_menu items never carry eta_minutes — real per-item ETA
    # doesn't exist there (confirmed live). Restaurant-level ETA does
    # exist via search_restaurants (deliveryTimeMinutes), so enrich items
    # with their restaurant's ETA by matching restaurant_id. Best-effort:
    # items whose restaurant doesn't show up in this second search (a
    # different result set) just keep eta_minutes=None.
    restaurants = await search_restaurants(client, query, address_id=address_id)
    eta_by_restaurant = {r.restaurant_id: r.eta_minutes for r in restaurants if r.eta_minutes}
    for item in items:
        if item.eta_minutes is None and item.restaurant_id in eta_by_restaurant:
            item.eta_minutes = eta_by_restaurant[item.restaurant_id]

    return filter_items(items, max_price=max_price, fastest=fastest, min_protein=min_protein)

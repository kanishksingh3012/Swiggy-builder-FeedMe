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
    client: MCPClient, query: str, address_id: str | None = None, offset: int = 0
) -> tuple[list[MenuItem], bool, int | None]:
    """Returns (items, has_more, next_offset). Confirmed live: search_menu
    is paginated (10 items/page) and supports an `offset` argument —
    a real query can have 100+ total matches with only the first page
    shown by default."""
    result = await client.call_tool("search_menu", query=query, addressId=address_id, offset=offset)
    content = structured_content(result)
    items = [MenuItem.model_validate(i) for i in content.get("items", [])]
    return items, bool(content.get("hasMore", False)), content.get("nextOffset")


async def search_restaurants(
    client: MCPClient, query: str, address_id: str | None = None
) -> list[Restaurant]:
    result = await client.call_tool("search_restaurants", query=query, addressId=address_id)
    restaurants = structured_content(result).get("restaurants", [])
    return [Restaurant.model_validate(r) for r in restaurants]


async def get_restaurant_menu(
    client: MCPClient, restaurant_id: str, address_id: str
) -> list[MenuItem]:
    """Confirmed live bug (2026-08-21): unlike search_menu, items here
    carry NO restaurant_id of their own at all — only the top-level
    `restaurant` object has one. cart.add_items() derives which
    restaurant to send from each item's restaurant_id, so without this
    backfill it silently ends up with none and place_food_order-adjacent
    calls fail with "restaurantId is required". Backfilling from the
    restaurant_id parameter (which the caller already knows, since it's
    required to make this call at all) rather than trying to trust
    anything per-item."""
    result = await client.call_tool(
        "get_restaurant_menu", restaurantId=restaurant_id, addressId=address_id
    )
    content = structured_content(result)
    restaurant = content.get("restaurant", {})
    categories = content.get("categories", [])
    raw_items = [i for category in categories for i in category.get("items", [])]
    items = [MenuItem.model_validate(i) for i in raw_items]
    for item in items:
        item.restaurant_id = restaurant_id
        item.restaurant_name = item.restaurant_name or restaurant.get("name")
        item.restaurant_rating = item.restaurant_rating or restaurant.get("avgRating")
    return items


def _contains_all_tokens(name: str | None, tokens: list[str]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return all(token in lowered for token in tokens)


def filter_items(
    items: list[MenuItem],
    *,
    query: str | None = None,
    max_price: float | None = None,
    fastest: bool = False,
    min_protein: float | None = None,
) -> list[MenuItem]:
    """Confirmed live (2026-08-21): search_menu's own relevance ranking
    can bury an exact match deep — "chicken burger" put a real item
    literally named "Chicken Fillet Burger" at rank 51 (page 6), while
    the fuller "chicken fillet burger" put the same item at rank 1. That
    ranking is entirely server-side and out of feedme's control, but
    within whatever page(s) actually get fetched, items whose name
    contains every query word (any order, not required to be adjacent —
    the actual generalization of the "consecutive words" theory this was
    built to test) are boosted to the front rather than left wherever
    Swiggy's own ranking put them relative to non-matching items. This
    doesn't retrieve items Swiggy never sent — `m` (pagination) still
    does that part."""
    filtered = items
    if max_price is not None:
        filtered = [i for i in filtered if i.price is None or i.price <= max_price]
    if min_protein is not None:
        filtered = [i for i in filtered if i.protein_g is not None and i.protein_g >= min_protein]

    tokens = [t for t in query.lower().split() if t] if query else []

    def sort_key(item: MenuItem) -> tuple[int, float]:
        relevance = 0 if (tokens and _contains_all_tokens(item.name, tokens)) else 1
        if not fastest:
            return (relevance, 0.0)
        eta = item.eta_minutes if item.eta_minutes is not None else float("inf")
        return (relevance, eta)

    if tokens or fastest:
        filtered = sorted(filtered, key=sort_key)
    return filtered


async def discover(
    client: MCPClient,
    query: str,
    *,
    address_id: str | None = None,
    offset: int = 0,
    max_price: float | None = None,
    fastest: bool = False,
    min_protein: float | None = None,
) -> tuple[list[MenuItem], bool, int | None]:
    """One page of search results, filtered, with real restaurant ETA
    merged in. Returns (items, has_more, next_offset) — pass next_offset
    back in as `offset` to fetch the next page (search_menu is
    paginated, confirmed live: a query can have 100+ total matches with
    only 10 returned per call)."""
    items, has_more, next_offset = await search_menu(
        client, query, address_id=address_id, offset=offset
    )

    # search_menu items never carry eta_minutes — real per-item ETA
    # doesn't exist there (confirmed live). Restaurant-level ETA does
    # exist via search_restaurants (deliveryTimeMinutes), so enrich items
    # with their restaurant's ETA by matching restaurant_id. Best-effort:
    # items whose restaurant doesn't show up in this second search (a
    # different result set) just keep eta_minutes/restaurant_rating=None.
    # Restaurant-level rating (avgRating) is enriched the same way —
    # search_menu's own "rating" field is a real per-dish rating
    # (confirmed live: two dishes from one restaurant showed different
    # values), which is a different, still-useful number kept as-is on
    # MenuItem.rating rather than overwritten.
    restaurants = await search_restaurants(client, query, address_id=address_id)
    eta_by_restaurant = {r.restaurant_id: r.eta_minutes for r in restaurants if r.eta_minutes}
    rating_by_restaurant = {r.restaurant_id: r.rating for r in restaurants if r.rating}
    for item in items:
        if item.eta_minutes is None and item.restaurant_id in eta_by_restaurant:
            item.eta_minutes = eta_by_restaurant[item.restaurant_id]
        if item.restaurant_id in rating_by_restaurant:
            item.restaurant_rating = rating_by_restaurant[item.restaurant_id]

    filtered = filter_items(
        items, query=query, max_price=max_price, fastest=fastest, min_protein=min_protein
    )
    return filtered, has_more, next_offset

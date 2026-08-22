from __future__ import annotations

from feedme.search import discover, filter_items, get_restaurant_menu
from models import MenuItem, Restaurant


def _item(
    item_id: str, price: float | None, eta: float | None, protein: float | None = None
) -> MenuItem:
    return MenuItem(item_id=item_id, price=price, eta_minutes=eta, protein_g=protein)


def test_filter_items_max_price():
    items = [_item("a", 100, 10), _item("b", 500, 5), _item("c", None, 20)]
    result = filter_items(items, max_price=400)
    assert {i.item_id for i in result} == {"a", "c"}


def test_filter_items_fastest_sorts_by_eta_ascending():
    items = [_item("a", 100, 30), _item("b", 100, 5), _item("c", 100, None)]
    result = filter_items(items, fastest=True)
    assert [i.item_id for i in result] == ["b", "a", "c"]


def test_filter_items_min_protein():
    items = [_item("a", 100, 10, protein=20), _item("b", 100, 10, protein=5), _item("c", 100, 10)]
    result = filter_items(items, min_protein=15)
    assert [i.item_id for i in result] == ["a"]


def test_filter_items_combination():
    items = [
        _item("a", 300, 10, protein=25),
        _item("b", 600, 5, protein=30),
        _item("c", 200, 40, protein=10),
    ]
    result = filter_items(items, max_price=400, fastest=True, min_protein=20)
    assert [i.item_id for i in result] == ["a"]


async def test_discover_enriches_items_with_restaurant_eta(monkeypatch):
    # search_menu items never carry eta_minutes for real (confirmed
    # live) — discover() enriches them from search_restaurants'
    # restaurant-level ETA, matched by restaurant_id.
    import feedme.search as search_mod

    async def fake_search_menu(client, query, address_id=None, offset=0):
        items = [
            MenuItem(item_id="i1", price=100, restaurant_id="r1"),
            MenuItem(item_id="i2", price=100, restaurant_id="r2"),
        ]
        return items, False, None

    async def fake_search_restaurants(client, query, address_id=None):
        return [Restaurant(id="r1", eta_minutes=25)]

    monkeypatch.setattr(search_mod, "search_menu", fake_search_menu)
    monkeypatch.setattr(search_mod, "search_restaurants", fake_search_restaurants)

    items, has_more, next_offset = await discover(None, "pizza")
    by_id = {i.item_id: i for i in items}
    assert by_id["i1"].eta_minutes == 25
    assert by_id["i2"].eta_minutes is None
    assert has_more is False
    assert next_offset is None


class _FakeClient:
    async def call_tool(self, tool_name, **kwargs):
        return {
            "structuredContent": {
                "restaurant": {"id": "r1", "name": "Test Place", "avgRating": 4.5},
                "categories": [
                    {"items": [{"id": "i1", "name": "Dish A", "price": 100}]},
                    {"items": [{"id": "i2", "name": "Dish B", "price": 200}]},
                ],
            }
        }


async def test_get_restaurant_menu_backfills_restaurant_id_onto_items():
    # Confirmed live 2026-08-21: unlike search_menu, get_restaurant_menu's
    # items carry no restaurant_id of their own — only the top-level
    # `restaurant` object has one. Without backfilling it, cart.add_items()
    # can't tell the server which restaurant the order is for, and
    # place_food_order-adjacent calls fail with "restaurantId is required"
    # (a real live failure this fix resolves).
    items = await get_restaurant_menu(_FakeClient(), "r1", "addr1")
    assert all(i.restaurant_id == "r1" for i in items)
    assert all(i.restaurant_name == "Test Place" for i in items)
    assert all(i.restaurant_rating == 4.5 for i in items)

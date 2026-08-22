from __future__ import annotations

from feedme.cli import _build_order_items
from models import MenuItem


def _first() -> MenuItem:
    return MenuItem(item_id="i0", name="Margherita Pizza", price=200, restaurant_id="r1")


def _menu() -> list[MenuItem]:
    return [
        MenuItem(item_id="i1", name="Garlic Naan", price=80, restaurant_id="r1"),
        MenuItem(item_id="i2", name="Cold Coffee", price=120, restaurant_id="r1"),
    ]


async def test_build_order_items_empty_menu_keeps_just_the_first_item(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return []

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "done")
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0"]


async def test_build_order_items_done_immediately_keeps_just_the_first_item(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "done")
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0"]


async def test_build_order_items_picks_two_more_dishes_then_done(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    # 'a' opens the menu sub-flow (not shown unless asked for), pick 1,
    # back to the order screen, 'a' again, pick 2, then done.
    responses = iter(["a", "1", "a", "2", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0", "i1", "i2"]


async def test_build_order_items_rejects_duplicate_pick(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["a", "1", "a", "1", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0", "i1"]


async def test_build_order_items_can_remove_an_added_dish(monkeypatch):
    # Confirmed missing before this: there was no way to undo a pick at
    # all except cancelling the whole order.
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["a", "1", "r2", "done"])  # add Garlic Naan, then remove it (position 2)
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0"]


async def test_build_order_items_can_remove_the_first_item_too(monkeypatch):
    # The first pick is no longer special-cased/unremovable — removing
    # everything (including it) signals full cancellation to the caller.
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["r1", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert result == []


async def test_build_order_items_remove_out_of_range_shows_error_and_continues(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return []

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["r5", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0"]


async def test_build_order_items_shows_more_dishes_only_five_at_a_time(monkeypatch):
    menu = [MenuItem(item_id=f"m{n}", name=f"Dish {n}", price=100) for n in range(7)]

    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return menu

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    # 'a' opens the menu; first screen only has 5 dishes (m0-m4), so
    # picking "6" should fail until 'm' reveals the rest.
    responses = iter(["a", "6", "m", "6", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _build_order_items(None, "addr1", _first())
    assert [i.item_id for i in result] == ["i0", "m5"]

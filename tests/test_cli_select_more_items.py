from __future__ import annotations

from feedme.cli import _select_more_items
from models import MenuItem


def _menu() -> list[MenuItem]:
    return [
        MenuItem(item_id="i1", name="Garlic Naan", price=80, restaurant_id="r1"),
        MenuItem(item_id="i2", name="Cold Coffee", price=120, restaurant_id="r1"),
    ]


async def test_select_more_items_empty_menu_returns_immediately(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return []

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    result = await _select_more_items(None, "addr1", "r1", "Test Restaurant")
    assert result == []


async def test_select_more_items_done_immediately_adds_nothing(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "done")
    result = await _select_more_items(None, "addr1", "r1", "Test Restaurant")
    assert result == []


async def test_select_more_items_picks_two_dishes_then_done(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["1", "2", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _select_more_items(None, "addr1", "r1", "Test Restaurant")
    assert [i.item_id for i in result] == ["i1", "i2"]


async def test_select_more_items_rejects_duplicate_pick(monkeypatch):
    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return _menu()

    monkeypatch.setattr("feedme.cli.search.get_restaurant_menu", fake_get_restaurant_menu)
    responses = iter(["1", "1", "done"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _select_more_items(None, "addr1", "r1", "Test Restaurant")
    assert [i.item_id for i in result] == ["i1"]

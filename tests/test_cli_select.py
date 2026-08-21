from __future__ import annotations

from feedme.cli import _select_item
from models import MenuItem


def _items() -> list[MenuItem]:
    return [
        MenuItem(item_id="i1", name="Margherita", price=150),
        MenuItem(item_id="i2", name="Pepperoni", price=250),
    ]


async def _no_more():
    raise AssertionError("fetch_more should not be called when has_more is False")


async def test_select_item_valid_choice(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "2")
    result = await _select_item(_items(), False, _no_more)
    assert result is not None
    assert result.item_id == "i2"


async def test_select_item_cancel_with_q(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "q")
    assert await _select_item(_items(), False, _no_more) is None


async def test_select_item_cancel_with_empty_default(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "")
    assert await _select_item(_items(), False, _no_more) is None


async def test_select_item_non_numeric_input_cancels(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "banana")
    assert await _select_item(_items(), False, _no_more) is None


async def test_select_item_out_of_range_cancels(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "99")
    assert await _select_item(_items(), False, _no_more) is None


async def test_select_item_loads_more_and_appends(monkeypatch):
    responses = iter(["m", "3"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))

    async def fetch_more():
        return [MenuItem(item_id="i3", name="Veggie", price=180)], False

    result = await _select_item(_items(), True, fetch_more)
    assert result is not None
    assert result.item_id == "i3"


async def test_select_item_more_when_none_available_reprompts(monkeypatch):
    responses = iter(["m", "1"])
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: next(responses))
    result = await _select_item(_items(), False, _no_more)
    assert result is not None
    assert result.item_id == "i1"

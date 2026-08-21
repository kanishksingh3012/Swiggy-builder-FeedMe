from __future__ import annotations

from feedme.cli import _select_item
from models import MenuItem


def _items() -> list[MenuItem]:
    return [
        MenuItem(item_id="i1", name="Margherita", price=150),
        MenuItem(item_id="i2", name="Pepperoni", price=250),
    ]


def test_select_item_valid_choice(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "2")
    result = _select_item(_items())
    assert result is not None
    assert result.item_id == "i2"


def test_select_item_cancel_with_q(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "q")
    assert _select_item(_items()) is None


def test_select_item_cancel_with_empty_default(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "")
    assert _select_item(_items()) is None


def test_select_item_non_numeric_input_cancels(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "banana")
    assert _select_item(_items()) is None


def test_select_item_out_of_range_cancels(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "99")
    assert _select_item(_items()) is None

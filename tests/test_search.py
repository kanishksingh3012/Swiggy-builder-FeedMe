from __future__ import annotations

from feedme.search import filter_items
from models import MenuItem


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

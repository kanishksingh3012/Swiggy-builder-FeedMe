from __future__ import annotations

from feedme.cli import _address_priority, _select_address
from models import Address


def _addr(id_: str, tag: str | None = None, category: str | None = None) -> Address:
    return Address(id=id_, addressTag=tag, addressCategory=category)


def test_address_priority_prefers_exact_home_tag_over_home_category():
    # Confirmed live: an account can have multiple addressCategory=="Home"
    # entries — the address tagged exactly "Home" is the real signal.
    home_tag = _addr("a1", tag="Home", category="Other")
    home_category_only = _addr("a2", tag="G Base", category="Home")
    other = _addr("a3", tag="Office", category="Other")
    ranked = sorted([other, home_category_only, home_tag], key=_address_priority)
    assert [a.id for a in ranked] == ["a1", "a2", "a3"]


def test_select_address_shows_only_top_three(monkeypatch):
    addresses = [_addr(f"a{i}", tag=f"t{i}", category="Other") for i in range(5)]
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "1")
    result = _select_address(addresses)
    assert result is not None
    assert result.id == "a0"


def test_select_address_cancel_with_q(monkeypatch):
    addresses = [_addr("a1"), _addr("a2")]
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "q")
    assert _select_address(addresses) is None


def test_select_address_out_of_range_cancels(monkeypatch):
    addresses = [_addr("a1")]
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "99")
    assert _select_address(addresses) is None

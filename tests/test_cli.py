from __future__ import annotations

from typer.testing import CliRunner

from feedme import cart, checkout, cli, search, tracking
from models import Address, Cart, MenuItem, Order, PaymentOption, TrackingStatus

runner = CliRunner()


class _FakeMCPClient:
    def __init__(self, server: str) -> None:
        self.server = server

    async def __aenter__(self) -> _FakeMCPClient:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


def _patch_pipeline(
    monkeypatch, *, zero_phone_available: bool = True, payment_option_count: int = 1
) -> None:
    async def fake_ensure_authenticated() -> None:
        return None

    monkeypatch.setattr(cli, "_ensure_authenticated", fake_ensure_authenticated)
    monkeypatch.setattr(cli, "MCPClient", _FakeMCPClient)

    async def fake_get_addresses(client):
        return [Address(id="a1", addressLine="123 Test St")]

    monkeypatch.setattr(search, "get_addresses", fake_get_addresses)

    async def fake_discover(
        client, query, *, address_id=None, offset=0, max_price=None, fastest=False
    ):
        items = [
            MenuItem(
                item_id="i1", name="Chicken Bowl", price=350, eta_minutes=20, restaurant_id="r1"
            )
        ]
        return items, False, None

    monkeypatch.setattr(search, "discover", fake_discover)

    async def fake_get_restaurant_menu(client, restaurant_id, address_id):
        return []

    monkeypatch.setattr(search, "get_restaurant_menu", fake_get_restaurant_menu)

    async def fake_flush_cart(client, address_id) -> None:
        return None

    async def fake_add_items(client, address_id, items):
        pricing = {"item_total": 350, "delivery_charge": 20, "taxes_and_charges": 30, "to_pay": 400}
        return Cart(address_id=address_id, subtotal=350, data={"pricing": pricing})

    async def fake_get_cart(client, address_id):
        return Cart(address_id=address_id, subtotal=350)

    async def fake_apply_best_coupon(client, cart_obj, restaurant_id):
        assert restaurant_id == "r1"
        return cart_obj

    monkeypatch.setattr(cart, "flush_cart", fake_flush_cart)
    monkeypatch.setattr(cart, "add_items", fake_add_items)
    monkeypatch.setattr(cart, "get_cart", fake_get_cart)
    monkeypatch.setattr(cart, "apply_best_coupon", fake_apply_best_coupon)

    async def fake_get_available_payment_options(client):
        if not zero_phone_available:
            raise checkout.NoUsablePaymentOptions("no usable options")
        all_options = [
            PaymentOption(method_id="s1", method_type="SWIGGY_MONEY", display_name="Swiggy Money"),
            PaymentOption(method_id="COD", method_type="COD", display_name="Pay on delivery"),
        ]
        return all_options[:payment_option_count]

    async def fake_checkout(client, cart_obj, payment_option):
        return Order(order_id="o1", status="PLACED", total_amount=350)

    monkeypatch.setattr(
        checkout, "get_available_payment_options", fake_get_available_payment_options
    )
    monkeypatch.setattr(checkout, "checkout", fake_checkout)

    async def fake_track_order(client, order_id, **kwargs):
        return TrackingStatus(order_id=order_id, status="DELIVERED")

    monkeypatch.setattr(tracking, "track_order", fake_track_order)


def test_cli_no_query_is_usage_error():
    result = runner.invoke(cli.app, [])
    assert result.exit_code != 0


def test_cli_runs_full_pipeline_after_confirming_item(monkeypatch):
    _patch_pipeline(monkeypatch)
    # "1\n" confirms the address, "1\n" picks the item, "\n" accepts the
    # order-builder's default ("done" — no more dishes to add/remove)
    result = runner.invoke(cli.app, ["chicken bowl", "--max-price", "400"], input="1\n1\n\n")
    assert result.exit_code == 0
    assert "Chicken Bowl" in result.stdout
    assert "To pay" in result.stdout
    assert "400" in result.stdout


def test_cli_load_more_fetches_several_pages_per_press(monkeypatch):
    _patch_pipeline(monkeypatch)
    calls = {"n": 0}

    async def fake_discover(
        client, query, *, address_id=None, offset=0, max_price=None, fastest=False
    ):
        calls["n"] += 1
        item = MenuItem(item_id=f"i{calls['n']}", name=f"Item {calls['n']}", price=100)
        # has_more True for the first 3 calls (initial page 0-2 within
        # fetch_more's batch), False after — confirms fetch_more loops
        # multiple pages per 'm' press rather than stopping at one.
        return [item], calls["n"] <= 3, calls["n"]

    monkeypatch.setattr(search, "discover", fake_discover)

    result = runner.invoke(cli.app, ["chicken bowl"], input="1\nm\nq\n")
    assert result.exit_code == 0
    # 1 initial call + up to PAGES_PER_LOAD_MORE(5) more from one 'm' press,
    # capped by has_more turning False after call 4 (3 more-pages + initial)
    assert calls["n"] >= 4


def test_cli_prompts_for_payment_method_when_multiple_available(monkeypatch):
    _patch_pipeline(monkeypatch, payment_option_count=2)
    # address, item, accept order-builder default, then pick payment method 2
    result = runner.invoke(cli.app, ["chicken bowl", "--max-price", "400"], input="1\n1\n\n2\n")
    assert result.exit_code == 0
    assert "payment" in result.stdout.lower()


def test_cli_cancelling_payment_selection_does_not_place_order(monkeypatch):
    _patch_pipeline(monkeypatch, payment_option_count=2)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("checkout.checkout should not run when payment selection is cancelled")

    monkeypatch.setattr(checkout, "checkout", fail_if_called)

    result = runner.invoke(cli.app, ["chicken bowl", "--max-price", "400"], input="1\n1\n\nq\n")
    assert result.exit_code == 0
    assert "not placed" in result.stdout.lower()


def test_cli_cancelling_selection_adds_nothing_to_cart(monkeypatch):
    _patch_pipeline(monkeypatch)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("cart.flush_cart should not run when the user cancels")

    monkeypatch.setattr(cart, "flush_cart", fail_if_called)

    # "1\n" confirms the address, "q\n" cancels the item pick
    result = runner.invoke(cli.app, ["chicken bowl"], input="1\nq\n")
    assert result.exit_code == 0
    assert "cancelled" in result.stdout.lower()


def test_cli_cancelling_address_confirmation_stops_before_anything_else(monkeypatch):
    _patch_pipeline(monkeypatch)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("search.discover should not run when address confirm is cancelled")

    monkeypatch.setattr(search, "discover", fail_if_called)

    result = runner.invoke(cli.app, ["chicken bowl"], input="q\n")
    assert result.exit_code == 0
    assert "no delivery address confirmed" in result.stdout.lower()


def test_cli_exits_nonzero_when_no_usable_payment_options(monkeypatch):
    _patch_pipeline(monkeypatch, zero_phone_available=False)
    result = runner.invoke(cli.app, ["chicken bowl"], input="1\n1\n\n")
    assert result.exit_code == 1
    assert "no usable options" in result.stdout.lower()

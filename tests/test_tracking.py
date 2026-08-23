from __future__ import annotations

from feedme.tracking import _final_message, _is_active, _status_line, track_order
from models import Order, TrackingStatus


def test_status_line_with_title_and_eta():
    status = TrackingStatus(order_id="o1", title="Preparing your order", eta_text="12 mins")
    assert _status_line(status) == "Preparing your order — 12 mins left"


def test_status_line_title_without_eta():
    status = TrackingStatus(order_id="o1", title="Driver is on the way")
    assert _status_line(status) == "Driver is on the way"


def test_status_line_no_title_uses_status_message():
    status = TrackingStatus(order_id="o1", status_message="No tracking information found")
    assert _status_line(status) == "No tracking information found"


def test_status_line_nothing_at_all():
    assert _status_line(TrackingStatus(order_id="o1")) == "Waiting for an update"


def test_is_active_true_while_entry_present():
    status = TrackingStatus(order_id="o1", title="Preparing your order", status="processing")
    assert _is_active(status)


def test_is_active_false_once_entry_disappears():
    # Confirmed live 2026-08-23: this is the real shape track_food_order
    # returns once an order concludes — no entry at all, not a terminal
    # status value inside one.
    status = TrackingStatus(order_id="o1", status_message="No tracking information found")
    assert not _is_active(status)


def test_is_active_false_for_terminal_status_on_a_present_entry():
    # Defensive case: never observed live, but handled in case a
    # terminal value is ever seen on a final entry instead of the entry
    # just disappearing.
    status = TrackingStatus(order_id="o1", title="Delivered", status="DELIVERED")
    assert not _is_active(status)


async def test_track_order_falls_back_to_get_food_orders_once_tracking_ends(monkeypatch):
    # Real flow: get_delivery_status reports an active entry, then goes
    # empty (order concluded) with no status of its own — the final
    # status has to come from get_food_orders instead.
    statuses = [
        TrackingStatus(order_id="o1", title="Preparing your order", status="processing"),
        TrackingStatus(order_id="o1", status_message="No tracking information found"),
    ]
    calls = {"n": 0}

    async def fake_get_delivery_status(client, order_id):
        status = statuses[calls["n"]]
        calls["n"] += 1
        return status

    async def fake_list_orders(client, address_id):
        assert address_id == "addr1"
        return [Order(orderId="o1", orderStatus="Delivered")]

    import feedme.tracking as tracking_mod

    monkeypatch.setattr(tracking_mod, "get_delivery_status", fake_get_delivery_status)
    monkeypatch.setattr(tracking_mod, "list_orders", fake_list_orders)

    result = await track_order(None, "o1", "addr1", poll_interval=0, render=False)
    assert result.status == "Delivered"
    assert calls["n"] == 2


async def test_track_order_stops_at_timeout(monkeypatch):
    async def fake_get_delivery_status(client, order_id):
        return TrackingStatus(order_id=order_id, title="Preparing your order", status="processing")

    import feedme.tracking as tracking_mod

    monkeypatch.setattr(tracking_mod, "get_delivery_status", fake_get_delivery_status)

    result = await track_order(None, "o1", "addr1", poll_interval=0, timeout=0, render=False)
    assert result.status == "processing"


def test_final_message_delivered():
    status = TrackingStatus(order_id="o1", status="Delivered")
    message, style = _final_message(status, timed_out=False)
    assert "delivered" in message.lower()
    assert style == "bold green"


def test_final_message_cancelled():
    status = TrackingStatus(order_id="o1", status="Cancelled")
    message, style = _final_message(status, timed_out=False)
    assert "cancelled" in message.lower()
    assert style == "bold red"


def test_final_message_timed_out_without_terminal_status():
    status = TrackingStatus(order_id="o1", title="Driver is on the way", status="processing")
    message, style = _final_message(status, timed_out=True)
    assert "Stopped tracking" in message
    assert style == "yellow"


async def test_track_order_prints_delivered_message(monkeypatch, capsys):
    async def fake_get_delivery_status(client, order_id):
        return TrackingStatus(order_id=order_id, status_message="No tracking information found")

    async def fake_list_orders(client, address_id):
        return [Order(orderId="o1", orderStatus="Delivered")]

    import feedme.tracking as tracking_mod

    monkeypatch.setattr(tracking_mod, "get_delivery_status", fake_get_delivery_status)
    monkeypatch.setattr(tracking_mod, "list_orders", fake_list_orders)

    await track_order(None, "o1", "addr1", poll_interval=0, render=True)
    captured = capsys.readouterr()
    assert "Order delivered" in captured.out

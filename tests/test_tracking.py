from __future__ import annotations

from feedme.tracking import _is_terminal, track_order
from models import TrackingStatus


def test_is_terminal_true_for_delivered():
    assert _is_terminal(TrackingStatus(order_id="o1", status="DELIVERED"))


def test_is_terminal_false_for_in_progress():
    assert not _is_terminal(TrackingStatus(order_id="o1", status="OUT_FOR_DELIVERY"))


def test_is_terminal_checks_stage_fallback():
    assert _is_terminal(TrackingStatus(order_id="o1", stage="cancelled"))


async def test_track_order_stops_on_terminal_status(monkeypatch):
    statuses = [
        TrackingStatus(order_id="o1", status="PLACED"),
        TrackingStatus(order_id="o1", status="OUT_FOR_DELIVERY"),
        TrackingStatus(order_id="o1", status="DELIVERED"),
    ]
    calls = {"n": 0}

    async def fake_get_delivery_status(client, order_id):
        status = statuses[calls["n"]]
        calls["n"] += 1
        return status

    import feedme.tracking as tracking_mod

    monkeypatch.setattr(tracking_mod, "get_delivery_status", fake_get_delivery_status)

    result = await track_order(None, "o1", poll_interval=0, render=False)
    assert result.status == "DELIVERED"
    assert calls["n"] == 3


async def test_track_order_stops_at_timeout(monkeypatch):
    async def fake_get_delivery_status(client, order_id):
        return TrackingStatus(order_id=order_id, status="PLACED")

    import feedme.tracking as tracking_mod

    monkeypatch.setattr(tracking_mod, "get_delivery_status", fake_get_delivery_status)

    result = await track_order(None, "o1", poll_interval=0, timeout=0, render=False)
    assert result.status == "PLACED"

from __future__ import annotations

from feedme.tracking import _is_terminal, _status_line, track_order
from models import TrackingStatus


def test_status_line_with_eta_and_known_stage():
    status = TrackingStatus(order_id="o1", stage="REACHING_RESTAURANT", eta_minutes=12)
    assert _status_line(status) == "12 min left — Driver is reaching the restaurant"


def test_status_line_without_eta_uses_friendly_stage_only():
    status = TrackingStatus(order_id="o1", status="OUT_FOR_DELIVERY")
    assert _status_line(status) == "Driver is on the way"


def test_status_line_unknown_stage_falls_back_to_humanized_text():
    status = TrackingStatus(order_id="o1", status="SOME_NEW_STAGE", eta_minutes=5)
    assert _status_line(status) == "5 min left — Some new stage"


def test_status_line_no_stage_uses_status_message():
    status = TrackingStatus(order_id="o1", status_message="No tracking information found")
    assert _status_line(status) == "No tracking information found"


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

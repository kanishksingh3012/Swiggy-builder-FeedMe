from __future__ import annotations

from feedme.cli import _select_payment_option
from models import PaymentOption


def _options() -> list[PaymentOption]:
    return [
        PaymentOption(method_id="wallet1", method_type="SWIGGY_MONEY", display_name="Swiggy Money"),
        PaymentOption(method_id="COD", method_type="COD", display_name="Pay on delivery"),
    ]


def test_single_option_is_used_without_prompting(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("should not prompt when there's only one option")

    monkeypatch.setattr("feedme.cli.typer.prompt", fail_if_called)
    only = [PaymentOption(method_id="COD", method_type="COD", display_name="Pay on delivery")]
    result = _select_payment_option(only)
    assert result is not None
    assert result.method_id == "COD"


def test_multiple_options_prompts_and_returns_choice(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "2")
    result = _select_payment_option(_options())
    assert result is not None
    assert result.method_id == "COD"


def test_multiple_options_cancel_with_q(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "q")
    assert _select_payment_option(_options()) is None


def test_multiple_options_out_of_range_cancels(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "99")
    assert _select_payment_option(_options()) is None


def _qr_option() -> PaymentOption:
    return PaymentOption(method_id="PayWithQR", method_type="UPI", display_name="Pay with QR")


def test_sole_qr_option_still_requires_explicit_confirmation(monkeypatch):
    # Confirmed live 2026-08-21: the old code auto-returned a sole option
    # with no prompt at all, regardless of type. A test script with too
    # few piped inputs sailed through this exact path when QR happened
    # to be the only option, generating a real UPI payment request with
    # nobody confirming anything. QR must always ask, even alone.
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "y")
    result = _select_payment_option([_qr_option()])
    assert result is not None
    assert result.method_id == "PayWithQR"


def test_sole_qr_option_declined_returns_none(monkeypatch):
    monkeypatch.setattr("feedme.cli.typer.prompt", lambda *a, **k: "n")
    assert _select_payment_option([_qr_option()]) is None


def test_sole_qr_option_prompt_defaults_to_no(monkeypatch):
    captured = {}

    def fake_prompt(*args, **kwargs):
        captured.update(kwargs)
        return kwargs.get("default", "")

    monkeypatch.setattr("feedme.cli.typer.prompt", fake_prompt)
    result = _select_payment_option([_qr_option()])
    assert captured.get("default") == "n"
    assert result is None

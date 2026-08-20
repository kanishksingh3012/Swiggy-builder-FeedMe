from __future__ import annotations

import pytest

from feedme.checkout import (
    ZeroPhonePaymentUnavailable,
    _is_zero_phone,
)
from models import PaymentOption


def _opt(
    method_type: str | None = None,
    method_id: str = "m1",
    display_name: str | None = None,
) -> PaymentOption:
    return PaymentOption(method_id=method_id, method_type=method_type, display_name=display_name)


def test_is_zero_phone_swiggy_money_variants():
    assert _is_zero_phone(_opt(method_type="swiggy_money"))
    assert _is_zero_phone(_opt(method_type="SwiggyMoney"))
    assert _is_zero_phone(_opt(method_type=None, display_name="Wallet"))


def test_is_zero_phone_cod_variants():
    assert _is_zero_phone(_opt(method_type="cod"))
    assert _is_zero_phone(_opt(method_type="Cash_On_Delivery"))


def test_is_zero_phone_rejects_upi_qr():
    assert not _is_zero_phone(_opt(method_type="upi"))
    assert not _is_zero_phone(_opt(method_type="qr_scan"))
    assert not _is_zero_phone(_opt(method_type=None, display_name="Scan & Pay"))


async def test_get_zero_phone_payment_options_filters_mixed(monkeypatch):
    from feedme import checkout

    options = [
        _opt(method_type="upi", method_id="u1"),
        _opt(method_type="swiggy_money", method_id="s1"),
        _opt(method_type="cod", method_id="c1"),
    ]

    async def fake_get_payment_options(client):
        return options

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    result = await checkout.get_zero_phone_payment_options(client=None)
    assert {o.method_id for o in result} == {"s1", "c1"}


async def test_get_zero_phone_payment_options_raises_when_only_upi(monkeypatch):
    from feedme import checkout

    options = [_opt(method_type="upi", method_id="u1")]

    async def fake_get_payment_options(client):
        return options

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    with pytest.raises(ZeroPhonePaymentUnavailable):
        await checkout.get_zero_phone_payment_options(client=None)


async def test_get_zero_phone_payment_options_raises_when_empty(monkeypatch):
    from feedme import checkout

    async def fake_get_payment_options(client):
        return []

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    with pytest.raises(ZeroPhonePaymentUnavailable):
        await checkout.get_zero_phone_payment_options(client=None)

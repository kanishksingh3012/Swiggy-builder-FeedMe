from __future__ import annotations

import pytest

from feedme.checkout import (
    NoUsablePaymentOptions,
    ZeroPhonePaymentUnavailable,
    _is_qr,
    _is_zero_phone,
    checkout,
    find_qr_payload,
)
from models import Cart, PaymentOption


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


def test_payment_option_parses_real_shape_and_filters_correctly():
    # Confirmed live 2026-08-21: real get_payment_options entries use
    # id/groupName/displayName, not method_id/method_type/display_name —
    # the model previously required "method_id" and crashed with a
    # pydantic ValidationError on every real entry. This locks the fix
    # in against the exact real shapes seen (no Swiggy Money existed on
    # the test account; only UPI intents, a desktop QR option, and COD).
    gpay = PaymentOption.model_validate(
        {"id": "gpay://upi/", "groupName": "UPI", "displayName": "Google Pay", "enabled": True}
    )
    qr = PaymentOption.model_validate(
        {"id": "PayWithQR", "groupName": "UPI", "displayName": "Pay with QR"}
    )
    cod = PaymentOption.model_validate(
        {"id": "COD", "groupName": "COD", "displayName": "Pay on delivery"}
    )

    assert not _is_zero_phone(gpay)
    assert not _is_zero_phone(qr)
    assert _is_zero_phone(cod)


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


def test_is_qr_recognizes_real_paywithqr_id():
    qr = PaymentOption.model_validate(
        {"id": "PayWithQR", "groupName": "UPI", "displayName": "Pay with QR"}
    )
    assert _is_qr(qr)


def test_is_qr_rejects_upi_app_intents_and_cod():
    gpay = PaymentOption.model_validate(
        {"id": "gpay://upi/", "groupName": "UPI", "displayName": "Google Pay"}
    )
    cod = PaymentOption.model_validate({"id": "COD", "groupName": "COD", "displayName": "COD"})
    assert not _is_qr(gpay)
    assert not _is_qr(cod)


async def test_get_available_payment_options_includes_qr_alongside_zero_phone(monkeypatch):
    from feedme import checkout

    options = [
        _opt(method_type="upi", method_id="gpay://upi/"),
        _opt(method_type="upi", method_id="PayWithQR"),
        _opt(method_type="cod", method_id="COD"),
    ]

    async def fake_get_payment_options(client):
        return options

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    result = await checkout.get_available_payment_options(client=None)
    assert {o.method_id for o in result} == {"PayWithQR", "COD"}


async def test_get_available_payment_options_raises_when_only_app_intents(monkeypatch):
    from feedme import checkout

    options = [_opt(method_type="upi", method_id="gpay://upi/")]

    async def fake_get_payment_options(client):
        return options

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    with pytest.raises(NoUsablePaymentOptions):
        await checkout.get_available_payment_options(client=None)


def test_find_qr_payload_checks_known_keys():
    assert find_qr_payload({"qrCodeUrl": "upi://pay?pa=x"}) == "upi://pay?pa=x"
    assert find_qr_payload({"unrelated": "value"}) is None


def test_find_qr_payload_searches_nested_dicts():
    data = {"data": {"payment": {"paymentLink": "upi://pay?pa=y"}}}
    assert find_qr_payload(data) == "upi://pay?pa=y"


async def test_get_zero_phone_payment_options_raises_when_empty(monkeypatch):
    from feedme import checkout

    async def fake_get_payment_options(client):
        return []

    monkeypatch.setattr(checkout, "get_payment_options", fake_get_payment_options)

    with pytest.raises(ZeroPhonePaymentUnavailable):
        await checkout.get_zero_phone_payment_options(client=None)


class _RecordingClient:
    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        default = {"structuredContent": {"orderId": "o1", "orderStatus": "PLACED"}}
        self._response = response or default

    async def call_tool(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        return self._response


async def test_checkout_cod_sends_cash_category_not_option_id():
    # Confirmed live 2026-08-21: place_food_order wants a broad category
    # ("Cash"), not the option's own id ("COD") — real error was "No
    # payment method selected" when the raw id was sent as paymentMethod.
    client = _RecordingClient()
    cart = Cart(address_id="addr1")
    cod = PaymentOption.model_validate({"id": "COD", "groupName": "COD", "displayName": "COD"})
    await checkout(client, cart, cod)
    tool_name, kwargs = client.calls[0]
    assert tool_name == "place_food_order"
    assert kwargs == {"addressId": "addr1", "paymentMethod": "Cash"}


async def test_checkout_qr_sends_upi_category_and_generate_flag():
    # Confirmed live 2026-08-21: sending paymentMethod="PayWithQR"
    # directly was rejected with "Unsupported payment method... Use
    # 'UPI' with intentApp/generateUPIQR for UPI payments."
    client = _RecordingClient()
    cart = Cart(address_id="addr1")
    qr = PaymentOption.model_validate(
        {"id": "PayWithQR", "groupName": "UPI", "displayName": "Pay with QR"}
    )
    await checkout(client, cart, qr)
    tool_name, kwargs = client.calls[0]
    assert tool_name == "place_food_order"
    assert kwargs == {"addressId": "addr1", "paymentMethod": "UPI", "generateUPIQR": True}

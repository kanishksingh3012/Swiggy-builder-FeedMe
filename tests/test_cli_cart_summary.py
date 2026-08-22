from __future__ import annotations

from feedme.cli import _print_cart_summary
from models import Cart


def test_print_cart_summary_shows_real_pricing_breakdown(capsys):
    cart = Cart(
        address_id="addr1",
        data={
            "pricing": {
                "item_total": 299,
                "delivery_charge": 38,
                "taxes_and_charges": 55.12,
                "to_pay": 392.12,
            }
        },
    )
    _print_cart_summary(cart)
    out = capsys.readouterr().out
    assert "299" in out
    assert "38" in out
    assert "55.12" in out
    assert "392.12" in out
    assert "To pay" in out


def test_print_cart_summary_shows_coupon_discount_when_present(capsys):
    pricing = {"item_total": 299, "delivery_charge": 0, "taxes_and_charges": 40, "to_pay": 209}
    cart = Cart(
        address_id="addr1",
        data={"pricing": pricing, "offers": {"coupon_discount": 130}},
    )
    _print_cart_summary(cart)
    out = capsys.readouterr().out
    assert "Coupon discount" in out
    assert "130" in out


def test_print_cart_summary_no_op_when_pricing_missing(capsys):
    cart = Cart(address_id="addr1")
    _print_cart_summary(cart)
    out = capsys.readouterr().out
    assert out == ""

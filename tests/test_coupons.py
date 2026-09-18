from datetime import date
from decimal import Decimal

import pytest

from src.coupons import Coupon, CouponError, apply_coupon, validate_code

TODAY = date(2026, 9, 18)


def coupon(code="SAVE10", percent_off=10, expires_on=date(2026, 12, 31)):
    return Coupon(code=code, percent_off=percent_off, expires_on=expires_on)


@pytest.mark.parametrize("raw,expected", [("SAVE10", "SAVE10"), (" save10 ", "SAVE10")])
def test_valid_codes_are_normalized(raw, expected):
    assert validate_code(raw) == expected


@pytest.mark.parametrize("raw", ["ABC", "THISCODEISWAYTOOLONG", "SAVE-10", "SAVE 10", ""])
def test_malformed_codes_are_rejected(raw):
    with pytest.raises(CouponError):
        validate_code(raw)


def test_discount_is_applied_and_rounded():
    assert apply_coupon(Decimal("99.99"), coupon(percent_off=10), TODAY) == Decimal("89.99")


def test_expired_coupon_is_rejected():
    expired = coupon(expires_on=date(2026, 9, 17))
    with pytest.raises(CouponError, match="expired"):
        apply_coupon(Decimal("50.00"), expired, TODAY)


def test_coupon_expiring_today_is_still_valid():
    assert apply_coupon(Decimal("100.00"), coupon(expires_on=TODAY), TODAY) == Decimal("90.00")


@pytest.mark.parametrize("percent_off", [0, 51, 100])
def test_out_of_range_discounts_are_rejected(percent_off):
    with pytest.raises(CouponError, match="outside allowed"):
        apply_coupon(Decimal("100.00"), coupon(percent_off=percent_off), TODAY)


def test_max_discount_never_goes_below_zero():
    assert apply_coupon(Decimal("10.00"), coupon(percent_off=50), TODAY) == Decimal("5.00")

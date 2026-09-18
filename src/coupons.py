"""Coupon code validation and discount application for checkout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CODE_PATTERN = re.compile(r"^[A-Z0-9]{4,12}$")
MAX_PERCENT_OFF = 50


class CouponError(ValueError):
    """Raised when a coupon cannot be applied."""


@dataclass(frozen=True)
class Coupon:
    code: str
    percent_off: int
    expires_on: date


def validate_code(code: str) -> str:
    """Return the normalized code, or raise CouponError.

    Codes are 4-12 characters, uppercase letters and digits only. Surrounding
    whitespace is ignored and lowercase input is upcased before checking.
    """
    normalized = code.strip().upper()
    if not CODE_PATTERN.match(normalized):
        raise CouponError(f"malformed coupon code: {code!r}")
    return normalized


def apply_coupon(subtotal: Decimal, coupon: Coupon, today: date) -> Decimal:
    """Return the discounted total, rounded to 2 decimal places.

    Raises CouponError if the coupon is expired or its discount is out of the
    allowed 1-50% range. The total never drops below zero.
    """
    validate_code(coupon.code)

    if coupon.expires_on < today:
        raise CouponError(f"coupon {coupon.code} expired on {coupon.expires_on}")

    if not 1 <= coupon.percent_off <= MAX_PERCENT_OFF:
        raise CouponError(
            f"discount {coupon.percent_off}% outside allowed 1-{MAX_PERCENT_OFF}%"
        )

    discount = subtotal * Decimal(coupon.percent_off) / Decimal(100)
    total = max(subtotal - discount, Decimal("0"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

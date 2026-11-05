"""Unit tests for app.pricing and the period buckets of app.reports."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.pricing import clamp_percent, discount_amount, price_appointment, resolve_discount
from app.reports import buckets_between, default_range, month_bucket, week_bucket

PLAN = SimpleNamespace(discount_percent=20)
OVERRIDES = [SimpleNamespace(specialty_id=2, percent=50), SimpleNamespace(specialty_id=3, percent=0)]


def test_resolve_discount_uses_plan_default():
    assert resolve_discount(PLAN, OVERRIDES, specialty_id=1) == 20


def test_resolve_discount_prefers_specialty_override():
    assert resolve_discount(PLAN, OVERRIDES, specialty_id=2) == 50
    assert resolve_discount(PLAN, OVERRIDES, specialty_id=3) == 0  # explicit zero wins over default


def test_resolve_discount_without_plan_is_zero():
    assert resolve_discount(None, OVERRIDES, specialty_id=2) == 0


def test_clamp_percent():
    assert clamp_percent(-5) == 0
    assert clamp_percent(150) == 100
    assert clamp_percent(33) == 33


@pytest.mark.parametrize(
    "gross, percent, expected",
    [(10000, 0, 0), (10000, 20, 2000), (10000, 100, 10000), (9999, 33, 3300), (1, 50, 1), (3, 50, 2)],
)
def test_discount_amount_rounds_half_up(gross, percent, expected):
    assert discount_amount(gross, percent) == expected


def test_price_appointment_breakdown():
    price = price_appointment(2000, 8000, 5000, 20)
    assert price.gross_cents == 15000
    assert price.discount_percent == 20
    assert price.discount_cents == 3000
    assert price.amount_cents == 12000
    assert (price.fee_cents, price.specialty_price_cents, price.professional_price_cents) == (2000, 8000, 5000)


def test_price_appointment_full_discount_is_free():
    assert price_appointment(2000, 8000, 5000, 100).amount_cents == 0


# --- report periods -----------------------------------------------------------------


def test_week_bucket_is_monday_to_sunday_with_iso_key():
    bucket = week_bucket(date(2026, 10, 22))  # Thursday
    assert (bucket.start, bucket.end, bucket.key) == (date(2026, 10, 19), date(2026, 10, 25), "2026-W43")
    assert week_bucket(date(2027, 1, 1)).key == "2026-W53"  # ISO year differs from calendar year


def test_month_bucket():
    bucket = month_bucket(date(2026, 2, 14))
    assert (bucket.start, bucket.end, bucket.key) == (date(2026, 2, 1), date(2026, 2, 28), "2026-02")
    assert month_bucket(date(2026, 12, 31)).end == date(2026, 12, 31)


def test_buckets_between_cover_range_including_partial_edges():
    weeks = buckets_between(date(2026, 10, 22), date(2026, 11, 3), "week")
    assert [b.key for b in weeks] == ["2026-W43", "2026-W44", "2026-W45"]
    months = buckets_between(date(2026, 10, 22), date(2027, 1, 3), "month")
    assert [b.key for b in months] == ["2026-10", "2026-11", "2026-12", "2027-01"]


def test_default_range():
    start, end = default_range("week", date(2026, 10, 22))
    assert end == date(2026, 10, 22)
    assert start == date(2026, 8, 31)  # Monday, 8 weeks including the current one
    start, end = default_range("month", date(2026, 10, 22))
    assert start == date(2026, 5, 1)


# --- on-call price list ------------------------------------------------------------


def test_price_components_regular_and_on_call():
    from app.pricing import on_call_value, price_components

    clinic = SimpleNamespace(fee_cents=2000, on_call_fee_cents=3000)
    specialty = SimpleNamespace(price_cents=8000, on_call_price_cents=None)  # unset: same as regular
    professional = SimpleNamespace(price_cents=5000, on_call_price_cents=9000)
    assert price_components(clinic, specialty, professional, on_call=False) == (2000, 8000, 5000)
    assert price_components(clinic, specialty, professional, on_call=True) == (3000, 8000, 9000)
    assert on_call_value(None, 700) == 700
    assert on_call_value(0, 700) == 0  # an explicit zero is a value


def test_urgent_rate_components_and_minimum():
    from app.pricing import URGENT_RATE_MULTIPLIER, price_components, urgent_minimum

    clinic = SimpleNamespace(fee_cents=2000, on_call_fee_cents=3000)
    specialty = SimpleNamespace(price_cents=8000, on_call_price_cents=10000)
    professional = SimpleNamespace(price_cents=5000, on_call_price_cents=9000, urgent_price_cents=15000)
    # Urgent: regular clinic/specialty values with the urgent rate, whatever the slot kind.
    assert price_components(clinic, specialty, professional, on_call=False, urgent=True) == (2000, 8000, 15000)
    assert price_components(clinic, specialty, professional, on_call=True, urgent=True) == (2000, 8000, 15000)
    assert URGENT_RATE_MULTIPLIER == 3
    assert urgent_minimum(5000) == 15000
    assert urgent_minimum(0) == 0

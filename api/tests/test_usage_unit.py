"""Unit tests for the plan appointment limit rules in app.usage (no HTTP, no database)."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from app.usage import (
    LIMIT_PER_MONTH,
    LIMIT_PER_SUBSCRIPTION,
    UNLIMITED,
    Usage,
    count_covered,
    counts_against_limit,
    usage_of,
    usage_window,
)


@dataclass
class Sub:
    joined_on: date = date(2026, 1, 10)
    expires_on: date = date(2027, 1, 9)


@dataclass
class Plan:
    appointment_limit: int = 2
    appointment_limit_period: str = LIMIT_PER_MONTH


@dataclass
class Appt:
    id: int
    marked_at: datetime
    status: str = "agendada"
    modality_id: int | None = 1
    over_limit: bool = False


# --- window ----------------------------------------------------------------------


def test_monthly_window_is_the_billing_period_of_the_date():
    assert usage_window(Sub(), LIMIT_PER_MONTH, date(2026, 10, 22)) == (date(2026, 10, 10), date(2026, 11, 9))
    assert usage_window(Sub(), LIMIT_PER_MONTH, date(2026, 10, 10)) == (date(2026, 10, 10), date(2026, 11, 9))
    assert usage_window(Sub(), LIMIT_PER_MONTH, date(2026, 10, 9)) == (date(2026, 9, 10), date(2026, 10, 9))
    # The last period is cut at the expiration.
    assert usage_window(Sub(), LIMIT_PER_MONTH, date(2027, 1, 5)) == (date(2026, 12, 10), date(2027, 1, 9))


def test_subscription_window_is_the_whole_period():
    assert usage_window(Sub(), LIMIT_PER_SUBSCRIPTION, date(2026, 10, 22)) == (date(2026, 1, 10), date(2027, 1, 9))


def test_window_is_none_outside_the_subscription():
    assert usage_window(Sub(), LIMIT_PER_MONTH, date(2026, 1, 9)) is None
    assert usage_window(Sub(), LIMIT_PER_SUBSCRIPTION, date(2027, 1, 10)) is None


def test_unknown_period_is_refused():
    with pytest.raises(ValueError):
        usage_window(Sub(), "week", date(2026, 10, 22))


# --- counting ----------------------------------------------------------------------


def test_what_counts_against_the_limit():
    assert counts_against_limit(Appt(1, datetime(2026, 10, 20, 8)))
    assert counts_against_limit(Appt(1, datetime(2026, 10, 20, 8), status="realizada"))
    assert not counts_against_limit(Appt(1, datetime(2026, 10, 20, 8), status="cancelada"))
    assert not counts_against_limit(Appt(1, datetime(2026, 10, 20, 8), modality_id=None))  # booked without a plan
    assert not counts_against_limit(Appt(1, datetime(2026, 10, 20, 8), over_limit=True))  # never took a place


def test_count_covered_filters_by_window_and_excludes_the_edited_appointment():
    rows = [
        Appt(1, datetime(2026, 10, 10, 8)),  # first day of the window
        Appt(2, datetime(2026, 11, 9, 23, 30)),  # last day of the window
        Appt(3, datetime(2026, 11, 10, 8)),  # next period
        Appt(4, datetime(2026, 10, 20, 8), status="cancelada"),
        Appt(5, datetime(2026, 10, 21, 8), over_limit=True),
    ]
    assert count_covered(rows, date(2026, 10, 10), date(2026, 11, 9)) == 2
    assert count_covered(rows, date(2026, 10, 10), date(2026, 11, 9), exclude_id=1) == 1
    assert count_covered([], date(2026, 10, 10), date(2026, 11, 9)) == 0


# --- usage ------------------------------------------------------------------------


def test_usage_of_a_limited_plan():
    rows = [Appt(1, datetime(2026, 10, 20, 8))]
    usage = usage_of(Plan(), Sub(), rows, date(2026, 10, 22))
    assert usage == Usage(limit=2, period=LIMIT_PER_MONTH, window_start=date(2026, 10, 10), window_end=date(2026, 11, 9), used=1)
    assert usage.remaining == 1
    assert usage.covered
    assert not usage.unlimited


def test_usage_exhausted_means_full_price_for_the_next_one():
    rows = [Appt(1, datetime(2026, 10, 20, 8)), Appt(2, datetime(2026, 10, 21, 8))]
    usage = usage_of(Plan(), Sub(), rows, date(2026, 10, 22))
    assert usage.used == 2
    assert usage.remaining == 0
    assert not usage.covered
    # Editing one of them: it does not count itself, so it stays covered.
    assert usage_of(Plan(), Sub(), rows, date(2026, 10, 22), exclude_id=2).covered


def test_usage_remaining_never_negative():
    rows = [Appt(i, datetime(2026, 10, 20, 8 + i)) for i in range(5)]
    usage = usage_of(Plan(), Sub(), rows, date(2026, 10, 22))
    assert usage.used == 5
    assert usage.remaining == 0


def test_unlimited_plan_is_always_covered():
    rows = [Appt(i, datetime(2026, 10, 20, 8 + i)) for i in range(5)]
    usage = usage_of(Plan(appointment_limit=0), Sub(), rows, date(2026, 10, 22))
    assert usage.unlimited
    assert usage.remaining is None
    assert usage.covered
    assert usage.used == 5  # still reported, for information
    assert UNLIMITED.covered and UNLIMITED.remaining is None


def test_usage_per_subscription_counts_the_whole_period():
    rows = [Appt(1, datetime(2026, 2, 1, 8)), Appt(2, datetime(2026, 9, 1, 8))]
    plan = Plan(appointment_limit=2, appointment_limit_period=LIMIT_PER_SUBSCRIPTION)
    usage = usage_of(plan, Sub(), rows, date(2026, 12, 1))
    assert (usage.window_start, usage.window_end) == (date(2026, 1, 10), date(2027, 1, 9))
    assert usage.used == 2
    assert not usage.covered


def test_usage_outside_the_subscription_has_no_window():
    usage = usage_of(Plan(), Sub(), [Appt(1, datetime(2026, 10, 20, 8))], date(2027, 5, 1))
    assert usage.window_start is None and usage.window_end is None
    assert usage.used == 0

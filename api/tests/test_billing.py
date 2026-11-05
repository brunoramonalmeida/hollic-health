"""Unit tests for the pure plan rules in app.billing (no HTTP, no database)."""

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.billing import (
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_OVERDUE,
    STATUS_PENDING,
    CoverageError,
    add_months,
    build_periods,
    check_coverage,
    first_unpaid_overdue,
    installment_status,
    monthly_amount,
    subscription_status,
    waiting_until,
)

TODAY = date(2026, 10, 22)


@dataclass
class Sub:
    joined_on: date = date(2026, 1, 10)
    expires_on: date = date(2027, 1, 9)
    cancelled_on: date | None = None


@dataclass
class Inst:
    sequence: int
    due_on: date
    paid_on: date | None = None


def installments(paid_through: int, total: int = 12, joined_on: date = date(2026, 1, 10)):
    return [
        Inst(i, add_months(joined_on, i - 1), add_months(joined_on, i - 1) if i <= paid_through else None)
        for i in range(1, total + 1)
    ]


# --- dates ------------------------------------------------------------------------


def test_add_months_keeps_day_and_rolls_year():
    assert add_months(date(2026, 1, 10), 1) == date(2026, 2, 10)
    assert add_months(date(2026, 1, 10), 12) == date(2027, 1, 10)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)


def test_build_periods_one_year_gives_twelve_months():
    periods = build_periods(date(2026, 1, 10), date(2027, 1, 9))
    assert len(periods) == 12
    assert (periods[0].sequence, periods[0].start, periods[0].end) == (1, date(2026, 1, 10), date(2026, 2, 9))
    assert (periods[-1].start, periods[-1].end) == (date(2026, 12, 10), date(2027, 1, 9))
    assert periods[0].due_on == date(2026, 1, 10)


def test_build_periods_cuts_last_period_at_expiration():
    periods = build_periods(date(2026, 1, 10), date(2026, 3, 20))
    assert [(p.start, p.end) for p in periods] == [
        (date(2026, 1, 10), date(2026, 2, 9)),
        (date(2026, 2, 10), date(2026, 3, 9)),
        (date(2026, 3, 10), date(2026, 3, 20)),
    ]


def test_build_periods_single_day_and_invalid_range():
    assert len(build_periods(date(2026, 1, 10), date(2026, 1, 10))) == 1
    with pytest.raises(ValueError):
        build_periods(date(2026, 1, 10), date(2026, 1, 9))


def test_waiting_until():
    assert waiting_until(date(2026, 1, 10), 30) == date(2026, 2, 9)
    assert waiting_until(date(2026, 1, 10), 0) == date(2026, 1, 10)
    assert waiting_until(date(2026, 1, 10), -5) == date(2026, 1, 10)


# --- amounts ----------------------------------------------------------------------


def test_monthly_amount_charges_only_dependents_above_the_limit():
    plan = SimpleNamespace(plan_value_cents=9900, dependent_limit=2, extra_dependent_cents=1500)
    assert monthly_amount(plan, 0) == 9900
    assert monthly_amount(plan, 2) == 9900
    assert monthly_amount(plan, 3) == 11400
    assert monthly_amount(plan, 5) == 14400


# --- installment and subscription status -------------------------------------------


def test_installment_status():
    assert installment_status(Inst(1, TODAY - timedelta(days=1), TODAY), TODAY) == "paid"
    assert installment_status(Inst(1, TODAY - timedelta(days=1)), TODAY) == "overdue"
    assert installment_status(Inst(1, TODAY), TODAY) == "open"  # due today is not overdue yet
    assert installment_status(Inst(1, TODAY + timedelta(days=1)), TODAY) == "open"


def test_first_unpaid_overdue_picks_the_oldest():
    items = [Inst(2, date(2026, 2, 10)), Inst(1, date(2026, 1, 10)), Inst(3, date(2026, 3, 10), date(2026, 3, 10))]
    assert first_unpaid_overdue(items, TODAY).sequence == 1
    assert first_unpaid_overdue([], TODAY) is None


def test_status_active_when_paid_through_current_month():
    assert subscription_status(Sub(), installments(paid_through=10), TODAY) == STATUS_ACTIVE


def test_status_pending_when_first_installment_unpaid():
    assert subscription_status(Sub(), installments(paid_through=0), TODAY) == STATUS_PENDING
    assert subscription_status(Sub(), [], TODAY) == STATUS_PENDING


def test_status_overdue_when_a_later_installment_is_past_due():
    assert subscription_status(Sub(), installments(paid_through=9), TODAY) == STATUS_OVERDUE


def test_status_not_overdue_for_installment_due_today_or_later():
    joined = TODAY - timedelta(days=1)
    items = [Inst(1, joined, joined), Inst(2, TODAY), Inst(3, add_months(joined, 2))]
    sub = Sub(joined_on=joined, expires_on=add_months(joined, 12))
    assert subscription_status(sub, items, TODAY) == STATUS_ACTIVE


def test_status_expired_beats_payment_state():
    sub = Sub(joined_on=date(2025, 1, 10), expires_on=date(2026, 1, 9))
    assert subscription_status(sub, installments(paid_through=12, joined_on=date(2025, 1, 10)), TODAY) == STATUS_EXPIRED
    assert subscription_status(sub, installments(paid_through=0, joined_on=date(2025, 1, 10)), TODAY) == STATUS_EXPIRED


def test_status_expired_only_after_expiration_day():
    sub = Sub(joined_on=date(2025, 10, 22), expires_on=TODAY)
    assert subscription_status(sub, installments(paid_through=13, joined_on=date(2025, 10, 22)), TODAY) == STATUS_ACTIVE


def test_status_cancelled_beats_everything():
    sub = Sub(cancelled_on=date(2026, 6, 1))
    assert subscription_status(sub, installments(paid_through=12), TODAY) == STATUS_CANCELLED


# --- coverage ---------------------------------------------------------------------


def covered(sub=Sub(), items=None, grace_days=30, when=date(2026, 10, 26), today=TODAY):
    check_coverage(sub, installments(paid_through=10) if items is None else items, grace_days, when, today)


def test_coverage_ok_for_active_subscription():
    covered()


def test_coverage_requires_a_subscription():
    with pytest.raises(CoverageError, match="no plan subscription"):
        covered(sub=None)


def test_coverage_rejects_pending_subscription():
    with pytest.raises(CoverageError, match="pending"):
        covered(items=installments(paid_through=0))


def test_coverage_rejects_overdue_subscription_with_due_date():
    with pytest.raises(CoverageError, match="overdue payment since 2026-10-10"):
        covered(items=installments(paid_through=9))


def test_coverage_rejects_cancelled_subscription():
    with pytest.raises(CoverageError, match="cancelled on 2026-06-01"):
        covered(sub=Sub(cancelled_on=date(2026, 6, 1)))


def test_coverage_rejects_expired_subscription():
    sub = Sub(joined_on=date(2025, 1, 10), expires_on=date(2026, 1, 9))
    with pytest.raises(CoverageError, match="expired on 2026-01-09"):
        covered(sub=sub, items=installments(paid_through=12, joined_on=date(2025, 1, 10)))


def test_coverage_rejects_appointment_after_expiration_even_if_active_today():
    with pytest.raises(CoverageError, match="expires on 2027-01-09, before the appointment"):
        covered(when=date(2027, 1, 10))


def test_coverage_allows_appointment_on_expiration_day():
    covered(when=date(2027, 1, 9))


def test_coverage_rejects_appointment_before_join_date():
    with pytest.raises(CoverageError, match="starts on 2026-01-10"):
        covered(when=date(2026, 1, 5))


def test_coverage_enforces_waiting_period():
    with pytest.raises(CoverageError, match="waiting period ends on 2026-02-09"):
        covered(when=date(2026, 2, 8), grace_days=30)
    covered(when=date(2026, 2, 9), grace_days=30)
    covered(when=date(2026, 1, 10), grace_days=0)


def test_coverage_payment_state_is_checked_before_dates():
    # Pending is reported even for an appointment inside the waiting period.
    with pytest.raises(CoverageError, match="pending"):
        covered(items=installments(paid_through=0), when=date(2026, 1, 15))


def test_coverage_error_status_code():
    assert CoverageError("x").status_code == 409

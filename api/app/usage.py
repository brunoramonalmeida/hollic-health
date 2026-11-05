"""Plan appointment limits: how many appointments a subscription covers.

A plan may cap the appointments it covers with its discount
(``Modality.appointment_limit``; 0 means unlimited) per
``appointment_limit_period``:

``month``
    each monthly billing period of the subscription (the same periods as
    the installments, anchored on the join day);
``subscription``
    the whole period ``[joined_on, expires_on]``.

The limit belongs to the holder's subscription, so it is shared by the
holder and their dependents. It never blocks scheduling: an appointment
booked once the allowance of its period is used up is still accepted, but
**pays the full price** (no plan discount) and is flagged ``over_limit``.

What counts against the allowance: every appointment of the customer in the
window that occupies a slot (scheduled or completed), was priced with the
plan (``modality_id`` set) and was itself inside the limit. Cancelled
appointments give their place back; over-limit ones never took one. Like the
prices, the decision is a snapshot taken when the appointment is (re)booked:
cancelling an earlier appointment does not retroactively discount a later
one, but editing that later one re-evaluates it.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from .billing import build_periods
from .models import Appointment
from .scheduling import occupies_slot

LIMIT_PER_MONTH = "month"
LIMIT_PER_SUBSCRIPTION = "subscription"
LIMIT_PERIODS = (LIMIT_PER_MONTH, LIMIT_PER_SUBSCRIPTION)


class PlanLike(Protocol):
    appointment_limit: int
    appointment_limit_period: str


class SubscriptionLike(Protocol):
    joined_on: date
    expires_on: date


class AppointmentLike(Protocol):
    id: int | None
    marked_at: datetime
    status: str
    modality_id: int | None
    over_limit: bool


@dataclass(frozen=True)
class Usage:
    """The allowance of one period and how much of it is taken."""

    limit: int  # 0: unlimited
    period: str
    window_start: date | None  # None: the date is outside the subscription
    window_end: date | None
    used: int  # covered appointments already booked in the window

    @property
    def unlimited(self) -> bool:
        return self.limit <= 0

    @property
    def remaining(self) -> int | None:
        """Covered appointments still available (None when unlimited)."""
        if self.unlimited:
            return None
        return max(self.limit - self.used, 0)

    @property
    def covered(self) -> bool:
        """Whether one more appointment in the window gets the plan discount."""
        return self.unlimited or self.used < self.limit


UNLIMITED = Usage(limit=0, period="", window_start=None, window_end=None, used=0)


def usage_window(subscription: SubscriptionLike, period: str, on: date) -> tuple[date, date] | None:
    """The date range whose allowance an appointment on ``on`` consumes, or
    None when ``on`` is outside the subscription."""
    if on < subscription.joined_on or on > subscription.expires_on:
        return None
    if period == LIMIT_PER_SUBSCRIPTION:
        return subscription.joined_on, subscription.expires_on
    if period == LIMIT_PER_MONTH:
        for item in build_periods(subscription.joined_on, subscription.expires_on):
            if item.start <= on <= item.end:
                return item.start, item.end
        return None
    raise ValueError(f"Unknown appointment limit period: {period}")


def counts_against_limit(row: AppointmentLike) -> bool:
    """Scheduled or completed, priced with a plan and inside its limit."""
    return occupies_slot(row.status) and row.modality_id is not None and not row.over_limit


def count_covered(rows: Iterable[AppointmentLike], start: date, end: date, exclude_id: int | None = None) -> int:
    """How many rows take a place of the allowance of ``[start, end]``."""
    total = 0
    for row in rows:
        if exclude_id is not None and row.id == exclude_id:
            continue
        if not counts_against_limit(row):
            continue
        if start <= row.marked_at.date() <= end:
            total += 1
    return total


def usage_of(
    plan: PlanLike, subscription: SubscriptionLike, rows: Iterable[AppointmentLike], on: date, exclude_id: int | None = None
) -> Usage:
    """Allowance of the period of ``on`` given the customer's appointments
    (``exclude_id``: the appointment being edited, so it does not count itself)."""
    limit = max(int(plan.appointment_limit or 0), 0)
    period = plan.appointment_limit_period or LIMIT_PER_MONTH
    window = usage_window(subscription, period, on)
    if window is None:
        return Usage(limit=limit, period=period, window_start=None, window_end=None, used=0)
    start, end = window
    return Usage(limit=limit, period=period, window_start=start, window_end=end, used=count_covered(rows, start, end, exclude_id))


# --- database -----------------------------------------------------------------


def customer_appointments(db: Session, customer_id: int, start: date, end: date) -> list[Appointment]:
    """The customer's appointments (holder and dependents) dated in [start, end]."""
    lower = datetime.combine(start, time.min)
    upper = datetime.combine(end + timedelta(days=1), time.min)
    return (
        db.query(Appointment)
        .filter(Appointment.customer_id == customer_id, Appointment.marked_at >= lower, Appointment.marked_at < upper)
        .all()
    )


def plan_usage(db: Session, subscription, on: date, exclude_id: int | None = None) -> Usage:
    """Usage of the subscription's allowance for an appointment on ``on``."""
    plan = subscription.modality
    window = usage_window(subscription, plan.appointment_limit_period or LIMIT_PER_MONTH, on)
    rows = customer_appointments(db, subscription.customer_id, *window) if window is not None else []
    return usage_of(plan, subscription, rows, on, exclude_id)


def usage_by_period(db: Session, subscription) -> dict[int, int]:
    """Covered appointments per installment sequence of the subscription."""
    rows = customer_appointments(db, subscription.customer_id, subscription.joined_on, subscription.expires_on)
    return {
        period.sequence: count_covered(rows, period.start, period.end)
        for period in build_periods(subscription.joined_on, subscription.expires_on)
    }

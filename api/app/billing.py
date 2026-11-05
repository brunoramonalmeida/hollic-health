"""Plan subscriptions, monthly installments and appointment coverage.

A subscription covers the closed period ``[joined_on, expires_on]`` and is
billed monthly: one installment per month of the period, due on the same day
of the month as ``joined_on``. The monthly amount is the plan value plus the
extra-dependent price for every dependent above the plan's limit, minus the
special discount of the seller's region when the plan was sold by a seller
(snapshotted on the subscription, see app.sales) — or, for a corporate
enrollment, the agreement's negotiated discount (also snapshotted, see
app.partners).

The subscription status is derived, never stored, so it cannot drift from the
payments::

    cancelada     cancelled_on is set
    migrada       replaced by a plan upgrade (upgraded_to_id) and past expires_on
    vencida       today is past expires_on
    pendente      the first installment has not been paid
    inadimplente  an installment is past due and unpaid
    ativa         everything else

An appointment can only be scheduled for a patient whose holder has an active
subscription that covers the appointment date, with the plan's waiting period
(``grace_days``, or the corporate agreement's own) elapsed. See
``check_coverage``.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from . import clock
from .models import Dependent, Installment, Modality, Subscription
from .pricing import discount_amount

STATUS_CANCELLED = "cancelada"
STATUS_EXPIRED = "vencida"
STATUS_PENDING = "pendente"
STATUS_OVERDUE = "inadimplente"
STATUS_ACTIVE = "ativa"
STATUS_UPGRADED = "migrada"
SUBSCRIPTION_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_OVERDUE, STATUS_EXPIRED, STATUS_CANCELLED, STATUS_UPGRADED)

INSTALLMENT_PAID = "paid"
INSTALLMENT_OVERDUE = "overdue"
INSTALLMENT_OPEN = "open"


class CoverageError(Exception):
    """The customer's plan does not allow the appointment."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InstallmentLike(Protocol):
    sequence: int
    due_on: date
    paid_on: date | None


class SubscriptionLike(Protocol):
    joined_on: date
    expires_on: date
    cancelled_on: date | None


@dataclass(frozen=True)
class Period:
    sequence: int
    start: date
    end: date

    @property
    def due_on(self) -> date:
        return self.start


# --- dates --------------------------------------------------------------------


def add_months(value: date, months: int) -> date:
    """Same day ``months`` later, clamped to the last day of the target month."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_periods(joined_on: date, expires_on: date) -> list[Period]:
    """Monthly periods covering [joined_on, expires_on]; the last one is cut at expires_on."""
    if expires_on < joined_on:
        raise ValueError("expires_on must not be before joined_on")
    periods = []
    sequence = 1
    start = joined_on
    while start <= expires_on:
        next_start = add_months(joined_on, sequence)
        end = min(next_start - timedelta(days=1), expires_on)
        periods.append(Period(sequence, start, end))
        sequence += 1
        start = next_start
    return periods


def waiting_until(joined_on: date, grace_days: int) -> date:
    """First day the plan can be used."""
    return joined_on + timedelta(days=max(grace_days, 0))


# --- amounts ------------------------------------------------------------------


def list_amount(modality: Modality, dependents_count: int) -> int:
    """Monthly fee at the list price: the plan value plus every extra dependent."""
    extra = max(dependents_count - modality.dependent_limit, 0)
    return modality.plan_value_cents + extra * modality.extra_dependent_cents


def monthly_amount(modality: Modality, dependents_count: int, discount_percent: int = 0) -> int:
    """Monthly fee charged: the list amount minus the special discount of the
    seller's region snapshotted on the subscription (see app.sales), rounded
    half up to the cent like the appointment discounts."""
    amount = list_amount(modality, dependents_count)
    return amount - discount_amount(amount, discount_percent)


# --- status -------------------------------------------------------------------


def installment_status(item: InstallmentLike, today: date) -> str:
    if item.paid_on is not None:
        return INSTALLMENT_PAID
    if item.due_on < today:
        return INSTALLMENT_OVERDUE
    return INSTALLMENT_OPEN


def first_unpaid_overdue(installments: Iterable[InstallmentLike], today: date) -> InstallmentLike | None:
    overdue = [item for item in installments if installment_status(item, today) == INSTALLMENT_OVERDUE]
    return min(overdue, key=lambda item: item.due_on) if overdue else None


def subscription_status(
    subscription: SubscriptionLike, installments: Iterable[InstallmentLike], today: date
) -> str:
    installments = list(installments)
    if subscription.cancelled_on is not None:
        return STATUS_CANCELLED
    if today > subscription.expires_on:
        return STATUS_UPGRADED if getattr(subscription, "upgraded_to_id", None) is not None else STATUS_EXPIRED
    first = min(installments, key=lambda item: item.sequence, default=None)
    if first is None or first.paid_on is None:
        return STATUS_PENDING
    if first_unpaid_overdue(installments, today) is not None:
        return STATUS_OVERDUE
    return STATUS_ACTIVE


# --- coverage -----------------------------------------------------------------


def check_coverage(
    subscription: SubscriptionLike | None,
    installments: Iterable[InstallmentLike],
    grace_days: int,
    appointment_date: date,
    today: date,
) -> None:
    """Raise ``CoverageError`` unless the plan allows an appointment on ``appointment_date``."""
    if subscription is None:
        raise CoverageError("Customer has no plan subscription")
    installments = list(installments)
    status = subscription_status(subscription, installments, today)
    if status == STATUS_CANCELLED:
        raise CoverageError(f"Subscription was cancelled on {subscription.cancelled_on:%Y-%m-%d}")
    if status == STATUS_EXPIRED:
        raise CoverageError(f"Subscription expired on {subscription.expires_on:%Y-%m-%d}")
    if status == STATUS_UPGRADED:
        raise CoverageError(f"Subscription was replaced by an upgrade on {subscription.expires_on + timedelta(days=1):%Y-%m-%d}")
    if status == STATUS_PENDING:
        raise CoverageError("Subscription is pending: the first payment was not received")
    if status == STATUS_OVERDUE:
        overdue = first_unpaid_overdue(installments, today)
        raise CoverageError(f"Subscription has an overdue payment since {overdue.due_on:%Y-%m-%d}")
    if appointment_date < subscription.joined_on:
        raise CoverageError(f"Subscription starts on {subscription.joined_on:%Y-%m-%d}")
    if appointment_date > subscription.expires_on:
        raise CoverageError(
            f"Subscription expires on {subscription.expires_on:%Y-%m-%d}, before the appointment"
        )
    usable_from = waiting_until(subscription.joined_on, grace_days)
    if appointment_date < usable_from:
        raise CoverageError(f"Plan waiting period ends on {usable_from:%Y-%m-%d}")


# --- database -----------------------------------------------------------------


def current_subscription(db: Session, customer_id: int, today: date | None = None) -> Subscription | None:
    """The customer's current non-cancelled subscription: the one in force
    today (a plan upgrade may be scheduled ahead, when the replaced plan is
    still the current one), else the latest."""
    today = today or clock.today()
    rows = (
        db.query(Subscription)
        .filter(Subscription.customer_id == customer_id, Subscription.cancelled_on.is_(None))
        .order_by(Subscription.joined_on.desc(), Subscription.id.desc())
        .all()
    )
    if not rows:
        return None
    return next((row for row in rows if row.joined_on <= today), rows[-1] if rows[-1].joined_on > today else rows[0])


def dependents_count(db: Session, customer_id: int) -> int:
    return db.query(Dependent).filter(Dependent.customer_id == customer_id).count()


def subscription_grace_days(db: Session, subscription: Subscription) -> int:
    """Waiting period of the subscription: the corporate agreement's own when
    it sets one (its terms are frozen, see app.partners), else the plan
    category's when it sets one, else the plan's (app.categories)."""
    from .categories import plan_grace_days

    modality = subscription.modality or db.get(Modality, subscription.modality_id)
    if subscription.agreement_id is None:
        return plan_grace_days(modality)
    from .partners import grace_days_for, load_agreement, offer_for  # partners imports this module

    agreement = load_agreement(db, subscription.agreement_id)
    return grace_days_for(offer_for(agreement.plans, subscription.modality_id), plan_grace_days(modality))


def is_split(subscription: Subscription) -> bool:
    """A corporate enrollment's fees are paid in two parts (company, employee)."""
    return subscription.agreement_id is not None


def locked(item: Installment) -> bool:
    """A fee with any payment is never re-priced, re-split nor dropped."""
    return item.paid_on is not None or item.company_paid_on is not None or item.employee_paid_on is not None


def _share_percent(db: Session, subscription: Subscription, period_start: date) -> int:
    """The company's part of the fee of a period: the split in force on its
    first day (the enrollment's history), else the agreement's default."""
    from .partners import default_share, load_agreement, offer_for, share_on  # partners imports this module

    percent = share_on(subscription.shares, period_start)
    if percent is not None:
        return percent
    agreement = load_agreement(db, subscription.agreement_id)
    return default_share(agreement, offer_for(agreement.plans, subscription.modality_id))


def _price(db: Session, subscription: Subscription, item: Installment, amount: int, discount: int) -> None:
    from .partners import split_cents  # partners imports this module

    item.amount_cents = amount
    item.discount_cents = discount
    item.company_share_cents = (
        split_cents(amount, _share_percent(db, subscription, item.period_start)) if is_split(subscription) else 0
    )


def sync_installments(db: Session, subscription: Subscription) -> None:
    """Make the installments match the period and the current monthly amount.

    Installments with any payment are never touched. Open ones are
    re-dated/re-priced (and, for a corporate enrollment, re-split between the
    company and the employee by the split in force on the first day of their
    period) and those no longer inside the period are removed; missing ones
    are added. The discount is the one snapshotted on the subscription (a
    seller's region's, or the corporate agreement's).
    """
    modality = subscription.modality or db.get(Modality, subscription.modality_id)
    dependents = dependents_count(db, subscription.customer_id)
    amount = monthly_amount(modality, dependents, subscription.discount_percent or 0)
    discount = list_amount(modality, dependents) - amount
    periods = {period.sequence: period for period in build_periods(subscription.joined_on, subscription.expires_on)}
    existing = {item.sequence: item for item in subscription.installments}
    for sequence, item in list(existing.items()):
        if locked(item):
            continue
        period = periods.get(sequence)
        if period is None:
            subscription.installments.remove(item)
            continue
        item.period_start, item.period_end, item.due_on = period.start, period.end, period.due_on
        _price(db, subscription, item, amount, discount)
    for sequence, period in periods.items():
        if sequence not in existing:
            item = Installment(sequence=sequence, period_start=period.start, period_end=period.end, due_on=period.due_on)
            _price(db, subscription, item, amount, discount)
            subscription.installments.append(item)


# --- paying a fee: in one go, or in two parts (company, employee) -------------------------

PART_COMPANY = "company"
PART_EMPLOYEE = "employee"
PARTS = (PART_COMPANY, PART_EMPLOYEE)


def part_cents(item: Installment, part: str) -> int:
    company = int(item.company_share_cents or 0)
    return company if part == PART_COMPANY else item.amount_cents - company


def part_paid(item: Installment, part: str) -> bool:
    """A part is settled once paid — or when there is nothing to pay in it."""
    paid_on = item.company_paid_on if part == PART_COMPANY else item.employee_paid_on
    return paid_on is not None or part_cents(item, part) == 0


def register_part(item: Installment, part: str, paid_on: date, cents: int | None, user_id: int | None) -> None:
    """Register the company's or the employee's part of a corporate fee
    (``cents`` default: the part's amount), then settle the fee."""
    amount = part_cents(item, part) if cents is None else cents
    if part == PART_COMPANY:
        item.company_paid_on, item.company_paid_cents, item.company_registered_by_id = paid_on, amount, user_id
    else:
        item.employee_paid_on, item.employee_paid_cents, item.employee_registered_by_id = paid_on, amount, user_id
    settle(item, user_id)


def undo_part(item: Installment, part: str) -> None:
    if part == PART_COMPANY:
        item.company_paid_on = item.company_paid_cents = item.company_registered_by_id = None
    else:
        item.employee_paid_on = item.employee_paid_cents = item.employee_registered_by_id = None
    settle(item, None)


def settle(item: Installment, user_id: int | None) -> None:
    """A corporate fee is paid once both parts are settled: on the later
    payment, for what both parts paid."""
    dates = [day for day in (item.company_paid_on, item.employee_paid_on) if day is not None]
    if dates and all(part_paid(item, part) for part in PARTS):
        item.paid_on = max(dates)
        item.paid_cents = (item.company_paid_cents or 0) + (item.employee_paid_cents or 0)
        item.registered_by_id = user_id or item.registered_by_id
    else:
        item.paid_on = item.paid_cents = None


def receipts(item: Installment) -> list[tuple[date, int]]:
    """The money received for a fee, by date: each part of a corporate fee
    on its own payment date, any other fee in one go."""
    if item.company_paid_on is not None or item.employee_paid_on is not None:
        parts = [(item.company_paid_on, item.company_paid_cents), (item.employee_paid_on, item.employee_paid_cents)]
        return [(day, int(cents or 0)) for day, cents in parts if day is not None and cents]
    if item.paid_on is not None:
        return [(item.paid_on, int(item.paid_cents or 0))]
    return []


def pay_in_full(subscription: Subscription, item: Installment, paid_on: date, user_id: int | None) -> None:
    """Register a whole fee (every part still open of a corporate one)."""
    if not is_split(subscription):
        item.paid_on, item.paid_cents, item.registered_by_id = paid_on, item.amount_cents, user_id
        return
    for part in PARTS:
        if not part_paid(item, part):
            register_part(item, part, paid_on, None, user_id)
    if item.paid_on is None:  # nothing to pay in either part (a fee of 0): record the date
        register_part(item, PART_EMPLOYEE, paid_on, 0, user_id)


def refresh_customer_installments(db: Session, customer_id: int) -> None:
    """Re-price unpaid installments after the customer's dependents changed."""
    subscription = current_subscription(db, customer_id)
    if subscription is not None:
        sync_installments(db, subscription)


def check_customer_coverage(
    db: Session, customer_id: int, appointment_date: date, today: date | None = None
) -> None:
    today = today or clock.today()
    subscription = current_subscription(db, customer_id)
    if subscription is None:
        # Only cancelled ones, if any: report the cancellation instead of "no plan".
        subscription = (
            db.query(Subscription)
            .filter(Subscription.customer_id == customer_id)
            .order_by(Subscription.id.desc())
            .first()
        )
    grace_days = subscription_grace_days(db, subscription) if subscription is not None else 0
    installments = subscription.installments if subscription is not None else []
    check_coverage(subscription, installments, grace_days, appointment_date, today)

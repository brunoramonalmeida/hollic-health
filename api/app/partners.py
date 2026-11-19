"""Corporate partnerships (B2B): partner companies, their agreements, the
rules of an employee's enrollment and the company's monthly invoice.

A **partner** is a company the network negotiates plans with directly, for
its employees. The negotiation is an **agreement**: a contract of **six
months** (``CONTRACT_MONTHS``; ``ends_on`` is derived from ``starts_on``)
listing the plans the employees may be enrolled in, each with negotiated
**conditions** (``AgreementPlan``):

* ``discount_percent`` — discount on the monthly fee (plan value plus the
  extra dependents), rounded half up like every other discount;
* ``grace_days`` — a waiting period of its own (null: the plan's — its
  category's when the category sets one, app.categories; 0: none).

**Frozen terms.** The conditions are snapshotted on every enrollment (the
subscription's ``discount_percent``, like a seller's sale) and the
agreement's terms — partner, start, minimum, payer, plans and conditions —
cannot change once an employee was enrolled (``terms_frozen``): a
renegotiation is a new contract. So an employee's fees never change with
the headcount or a later negotiation.

**The minimum of employees.** The conditions are granted in exchange of a
minimum number of employees (``min_employees``), measured **per month**:
the headcount of a month is how many employees (holders; dependents do not
count) have a fee of the agreement **due in that month**. The employees
always get the negotiated conditions; for every month whose headcount is
below the minimum, the company pays Hollic Health **the difference** — the
discounts granted on that month's fees (``shortfall``). The shortfall of a
month is projected while the month runs and due once it ended (on day
``SHORTFALL_DUE_DAY`` of the next month); its payment is recorded with the
amount and headcount of the moment (``AgreementCharge``).

**Who pays: the company, the employee, or both (co-payment).** ``payer:
company`` — the company pays every fee of its employees; ``payer:
employee`` — each employee pays its own fees at the negotiated price;
``payer: shared`` — a **co-payment**: each fee is split between the company
(``company_share_percent`` of it, rounded half up) and the employee (the
rest). The contract sets the default split (``Agreement.company_share_percent``,
or the plan's own ``AgreementPlan.company_share_percent``), frozen like the
other terms; the split of each employee is then **agreed between the company
and the employee** and may change over time (``EnrollmentShare``: from a
date on; the company in its portal, or the staff). The split in force on the
first day of a fee's period splits it (``share_on``); a fee with a part
already paid keeps its split. Each part is paid on its own — the company's
through its invoice, the employee's on its own — and the fee is paid once
both are in (app.billing).

**The company's invoice.** Each month with fees due is one invoice: the
company's part of its employees' fees due in the month (all of them when it
pays them; none when the employees do) plus the shortfall, if any.

An enrollment (``Subscription.agreement_id``) must use a plan the agreement
covers and fit inside its six months; a new one also needs the agreement and
its partner to be active and the agreement not ended (``check_enrollment``,
``EnrollmentError`` → HTTP 409). Corporate enrollments are never a
seller's sale; the staff records them, and the company itself through its
self-service portal (role ``partner``).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Protocol

from sqlalchemy.orm import Session, joinedload, selectinload

from .models import Agreement, AgreementPlan, Partner, Subscription

PAYER_COMPANY = "company"
PAYER_SHARED = "shared"  # co-payment: the company and the employee split each fee
PAYER_EMPLOYEE = "employee"
PAYERS = (PAYER_COMPANY, PAYER_SHARED, PAYER_EMPLOYEE)

CONTRACT_MONTHS = 6
SHORTFALL_DUE_DAY = 10  # the shortfall of a month is due on this day of the next month

AGREEMENT_SCHEDULED = "scheduled"  # not started yet (enrollments may be recorded ahead)
AGREEMENT_IN_FORCE = "in_force"
AGREEMENT_ENDED = "ended"
AGREEMENT_SUSPENDED = "suspended"  # the agreement or its partner is inactive
AGREEMENT_STATUSES = (AGREEMENT_SCHEDULED, AGREEMENT_IN_FORCE, AGREEMENT_ENDED, AGREEMENT_SUSPENDED)

INVOICE_PAID = "paid"  # nothing left for the company to pay
INVOICE_OPEN = "open"
INVOICE_OVERDUE = "overdue"
INVOICE_NOTHING_DUE = "nothing_due"  # the employees pay their fees and there is no shortfall


class EnrollmentError(Exception):
    """The enrollment is outside what the agreement allows."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OfferLike(Protocol):
    modality_id: int
    discount_percent: int
    grace_days: int | None
    company_share_percent: int | None


class ShareLike(Protocol):
    company_share_percent: int
    effective_from: date


# --- the contract ---------------------------------------------------------------


def add_months(value: date, months: int) -> date:
    """Same day ``months`` later, clamped to the last day of the target month."""
    from .billing import add_months as billing_add_months  # billing imports this module

    return billing_add_months(value, months)


def contract_end(starts_on: date) -> date:
    """Last day of a contract starting on ``starts_on``: six months."""
    return add_months(starts_on, CONTRACT_MONTHS) - timedelta(days=1)


def offer_for(plans: Iterable[OfferLike], modality_id: int) -> OfferLike | None:
    """The agreement's conditions for a plan, or None when it does not cover it."""
    for item in plans:
        if item.modality_id == modality_id:
            return item
    return None


def grace_days_for(offer: OfferLike | None, plan_grace_days: int) -> int:
    """Waiting period of an enrollment: the agreement's own, when it sets one."""
    if offer is None or offer.grace_days is None:
        return max(int(plan_grace_days or 0), 0)
    return max(int(offer.grace_days), 0)


def terms_of(agreement) -> tuple:
    """What is frozen once employees are enrolled (the default split too:
    the split of each employee then changes with ``EnrollmentShare``)."""
    return (
        agreement.partner_id,
        agreement.starts_on,
        int(agreement.min_employees),
        agreement.payer,
        int(contract_share(agreement)),
        tuple(
            sorted(
                (item.modality_id, int(item.discount_percent or 0), item.grace_days, getattr(item, "company_share_percent", None))
                for item in agreement.plans
            )
        ),
    )


# --- who pays: the split of each fee ---------------------------------------------------


def contract_share(agreement) -> int:
    """The agreement's default company part: 100 when the company pays, 0
    when the employees do, the negotiated default in a co-payment."""
    if agreement.payer == PAYER_COMPANY:
        return 100
    if agreement.payer == PAYER_EMPLOYEE:
        return 0
    return max(0, min(100, int(agreement.company_share_percent or 0)))


def default_share(agreement, offer: OfferLike | None) -> int:
    """The company's part of a new enrollment's fees: the plan's own in a
    co-payment when it sets one, else the agreement's."""
    if agreement.payer == PAYER_SHARED and offer is not None and getattr(offer, "company_share_percent", None) is not None:
        return max(0, min(100, int(offer.company_share_percent)))
    return contract_share(agreement)


def share_on(history: Iterable[ShareLike], day: date) -> int | None:
    """The company's part in force on ``day``: the latest split effective on
    or before it (the first one before any). None without history."""
    rows = sorted(history, key=lambda row: row.effective_from)
    if not rows:
        return None
    current = rows[0]
    for row in rows:
        if row.effective_from <= day:
            current = row
    return int(current.company_share_percent)


def split_cents(amount_cents: int, company_percent: int) -> int:
    """The company's part of a fee, rounded half up; the employee pays the rest."""
    pct = max(0, min(100, int(company_percent)))
    return (max(int(amount_cents), 0) * pct + 50) // 100


def is_suspended(agreement) -> bool:
    partner = getattr(agreement, "partner", None)
    return not agreement.active or (partner is not None and not partner.active)


def agreement_status(agreement, today: date) -> str:
    if is_suspended(agreement):
        return AGREEMENT_SUSPENDED
    if today < agreement.starts_on:
        return AGREEMENT_SCHEDULED
    if today > agreement.ends_on:
        return AGREEMENT_ENDED
    return AGREEMENT_IN_FORCE


def payer_label(payer: str, company_percent: int | None = None) -> str:
    if payer == PAYER_COMPANY:
        return "paid by the company"
    if payer == PAYER_SHARED:
        if company_percent is None:
            return "co-paid by the company and the employee"
        return f"co-paid: {company_percent}% by the company, {100 - company_percent}% by the employee"
    return "paid by the employee"


def check_enrollment(agreement, modality, joined_on: date, expires_on: date, today: date, new: bool = True) -> OfferLike:
    """The agreement's conditions for enrolling in ``modality`` for
    ``[joined_on, expires_on]``, or ``EnrollmentError``.

    ``new``: a new enrollment (or one moved to this agreement) also needs the
    agreement and its partner active and the agreement not ended; editing an
    enrollment already there (its dates or plan) only checks the plan and
    the period, so a suspended agreement keeps serving the employees it has."""
    partner_name = agreement.partner.name if getattr(agreement, "partner", None) is not None else "the partner"
    if new:
        if getattr(agreement, "partner", None) is not None and not agreement.partner.active:
            raise EnrollmentError(f"Partner {partner_name} is inactive: no new enrollments")
        if not agreement.active:
            raise EnrollmentError(f'Agreement "{agreement.name}" is suspended: no new enrollments')
        if today > agreement.ends_on:
            raise EnrollmentError(f'Agreement "{agreement.name}" ended on {agreement.ends_on:%Y-%m-%d}')
    offer = offer_for(agreement.plans, modality.id)
    if offer is None:
        raise EnrollmentError(f'Plan "{modality.name}" is not covered by agreement "{agreement.name}" ({partner_name})')
    if joined_on < agreement.starts_on or expires_on > agreement.ends_on:
        raise EnrollmentError(
            f'The enrollment must fit in agreement "{agreement.name}": '
            f"{agreement.starts_on:%Y-%m-%d} to {agreement.ends_on:%Y-%m-%d}"
        )
    return offer


# --- months, headcount and shortfall ----------------------------------------------------


def invoice_period(day: date) -> str:
    """The invoice an installment belongs to: the month it is due (``YYYY-MM``)."""
    return f"{day:%Y-%m}"


def parse_period(value: str) -> tuple[date, date] | None:
    """``YYYY-MM`` -> first and last day of the month (None when malformed)."""
    try:
        year, month = (int(part) for part in value.split("-"))
        start = date(year, month, 1)
    except (ValueError, TypeError):
        return None
    following = date(year + (month == 12), month % 12 + 1, 1)
    return start, following - timedelta(days=1)


def shortfall_due_on(period: str) -> date:
    """The shortfall of a month is due on day ``SHORTFALL_DUE_DAY`` of the next month."""
    _, end = parse_period(period)
    following = end + timedelta(days=1)
    return following.replace(day=SHORTFALL_DUE_DAY)


def reference_period(agreement, today: date) -> str:
    """The month whose headcount tells whether the minimum is met now:
    today's, clamped into the agreement's months."""
    day = min(max(today, agreement.starts_on), agreement.ends_on)
    return invoice_period(day)


class Ledger:
    """The fees of an agreement's enrollments grouped by the month they are
    due: the company's invoices and the headcount of each month.
    ``enrollments``: subscriptions with their installments."""

    def __init__(self, enrollments: Iterable):
        self.enrollments = list(enrollments)
        self.by_month: dict[str, list] = defaultdict(list)
        for row in self.enrollments:
            for item in row.installments:
                self.by_month[invoice_period(item.due_on)].append((row, item))

    def lines(self, period: str) -> list:
        return sorted(
            self.by_month.get(period, []),
            key=lambda pair: (pair[1].due_on, pair[0].customer.name.lower() if pair[0].customer else "", pair[1].sequence),
        )

    def employees(self, period: str, exclude_customer_id: int | None = None) -> int:
        """Employees billed in the month (each counts once, whatever its fees)."""
        return len(
            {row.customer_id for row, _ in self.by_month.get(period, []) if row.customer_id != exclude_customer_id}
        )

    def meets(self, minimum: int, period: str) -> bool:
        return self.employees(period) >= max(int(minimum or 0), 1)

    def periods(self) -> list[str]:
        return sorted(self.by_month)


@dataclass
class InvoiceTotals:
    """The company's invoice of a month: its part of its employees' fees due
    in the month and the shortfall when they are fewer than the minimum.
    The employees' parts are summed too, for the record."""

    period: str
    payer: str
    min_employees: int
    installments: int = 0
    employees: int = 0
    list_cents: int = 0
    discount_cents: int = 0
    amount_cents: int = 0  # the fees, at the negotiated price (both parts)
    company_share_cents: int = 0  # the company's part of the fees
    employee_share_cents: int = 0  # the employees' parts
    company_fees_paid_cents: int = 0
    company_fees_open_cents: int = 0
    company_open_count: int = 0
    company_overdue_count: int = 0
    employee_paid_cents: int = 0
    employee_open_cents: int = 0
    first_due_on: date | None = None
    last_due_on: date | None = None
    company_paid_on: date | None = None  # latest payment of the company's part of the month
    month_ended: bool = False
    shortfall_cents: int = 0  # projected while the month runs; the paid amount once paid
    shortfall_due_on: date | None = None
    shortfall_paid_on: date | None = None

    @property
    def below_minimum(self) -> bool:
        return self.employees < max(self.min_employees, 1)

    @property
    def shortfall_open(self) -> bool:
        return self.shortfall_cents > 0 and self.shortfall_paid_on is None

    @property
    def shortfall_payable(self) -> bool:
        """A shortfall is charged once its month is over (the headcount is final)."""
        return self.shortfall_open and self.month_ended

    @property
    def company_cents(self) -> int:
        """What the company owes for the month: its part of the fees and the shortfall."""
        return self.company_share_cents + self.shortfall_cents

    @property
    def company_open_cents(self) -> int:
        return self.company_fees_open_cents + (self.shortfall_cents if self.shortfall_open else 0)

    def status(self, today: date) -> str:
        if not self.company_open_count and not self.shortfall_open:
            return INVOICE_PAID if self.company_cents > 0 else INVOICE_NOTHING_DUE
        if self.company_overdue_count or (
            self.shortfall_payable and self.shortfall_due_on is not None and today > self.shortfall_due_on
        ):
            return INVOICE_OVERDUE
        return INVOICE_OPEN


def invoice_totals(period: str, lines: Iterable, today: date, minimum: int, payer: str, charge=None) -> InvoiceTotals:
    """``lines``: (subscription, installment) pairs due in the month;
    ``charge``: the shortfall payment recorded for it, if any (its amount is
    then the month's shortfall, frozen). Each installment carries the
    company's part (``company_share_cents``) and the payment of each part."""
    totals = InvoiceTotals(period=period, payer=payer, min_employees=int(minimum or 0))
    employees = set()
    for subscription, item in lines:
        employees.add(subscription.customer_id)
        company = int(item.company_share_cents or 0)
        employee = item.amount_cents - company
        totals.installments += 1
        totals.amount_cents += item.amount_cents
        totals.discount_cents += item.discount_cents or 0
        totals.list_cents += item.amount_cents + (item.discount_cents or 0)
        totals.company_share_cents += company
        totals.employee_share_cents += employee
        if company:
            if item.company_paid_on is not None:
                totals.company_fees_paid_cents += item.company_paid_cents or 0
                totals.company_paid_on = max(totals.company_paid_on or item.company_paid_on, item.company_paid_on)
            else:
                totals.company_fees_open_cents += company
                totals.company_open_count += 1
                if item.due_on < today:
                    totals.company_overdue_count += 1
        if employee:
            if item.employee_paid_on is not None:
                totals.employee_paid_cents += item.employee_paid_cents or 0
            else:
                totals.employee_open_cents += employee
        totals.first_due_on = min(totals.first_due_on or item.due_on, item.due_on)
        totals.last_due_on = max(totals.last_due_on or item.due_on, item.due_on)
    totals.employees = len(employees)
    bounds = parse_period(period)
    totals.month_ended = bounds is not None and today > bounds[1]
    totals.shortfall_due_on = shortfall_due_on(period)
    if charge is not None:
        totals.shortfall_cents = charge.amount_cents
        totals.shortfall_paid_on = charge.paid_on
    elif totals.below_minimum:
        totals.shortfall_cents = totals.discount_cents
    return totals


# --- database -------------------------------------------------------------------------


def agreement_query(db: Session):
    return db.query(Agreement).options(
        joinedload(Agreement.partner),
        selectinload(Agreement.plans).joinedload(AgreementPlan.modality),
        selectinload(Agreement.charges),
    )


def load_agreement(db: Session, agreement_id: int) -> Agreement | None:
    return agreement_query(db).filter(Agreement.id == agreement_id).first()


def enrollments_query(db: Session):
    return db.query(Subscription).options(
        joinedload(Subscription.customer),
        joinedload(Subscription.modality),
        selectinload(Subscription.installments),
        selectinload(Subscription.shares),
    )


def ledger(db: Session, agreement_id: int) -> Ledger:
    """Every enrollment of the agreement with its fees (cancelled ones too:
    what they still owe). Pending changes must be flushed."""
    return Ledger(enrollments_query(db).filter(Subscription.agreement_id == agreement_id).all())


def ledgers(db: Session, agreement_ids=None) -> dict[int, Ledger]:
    """agreement id -> its ledger (one query for every agreement asked)."""
    query = enrollments_query(db).filter(Subscription.agreement_id.is_not(None))
    if agreement_ids is not None:
        query = query.filter(Subscription.agreement_id.in_(sorted(agreement_ids)))
    grouped: dict[int, list] = defaultdict(list)
    for row in query.all():
        grouped[row.agreement_id].append(row)
    return defaultdict(lambda: Ledger([]), {key: Ledger(rows) for key, rows in grouped.items()})


def has_enrollments(db: Session, agreement_id: int) -> bool:
    return db.query(Subscription.id).filter(Subscription.agreement_id == agreement_id).first() is not None


def partner_query(db: Session):
    return db.query(Partner).options(
        selectinload(Partner.agreements).selectinload(Agreement.plans).joinedload(AgreementPlan.modality)
    )


@dataclass(frozen=True)
class MinimumState:
    """Whether the agreement meets its minimum in the current month, before
    and after a change (to tell the staff and the company when it flips)."""

    agreement_id: int
    period: str
    employees: int
    was_met: bool
    met: bool

    @property
    def changed(self) -> bool:
        return self.was_met != self.met


def refresh_minimum(db: Session, agreement: Agreement, people: Ledger, today: date) -> MinimumState:
    """Record whether the agreement meets its minimum this month."""
    period = reference_period(agreement, today)
    was_met, met = bool(agreement.conditions_met), people.meets(agreement.min_employees, period)
    agreement.conditions_met = met
    return MinimumState(agreement.id, period, people.employees(period), was_met, met)


def watch_agreements(db: Session, today: date) -> int:
    """Worker check (every pass) of each agreement's minimum in the current
    month. A new month, a cancellation or an enrollment ending changes it
    without anyone touching the agreement: when it flips, the staff and the
    company are told, once. Returns how many agreements changed."""
    from .notifications import agreement_minimum_changed  # notifications imports this module

    books = ledgers(db)
    changed = 0
    for agreement in agreement_query(db).all():
        state = refresh_minimum(db, agreement, books[agreement.id], today)
        if not state.changed:
            continue
        if not is_suspended(agreement) and today <= agreement.ends_on:
            agreement_minimum_changed(db, agreement, state, None)
        changed += 1
    db.commit()
    return changed

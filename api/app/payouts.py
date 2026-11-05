"""Paying professionals and clinics: advances, settlements, installments, receipts.

The network collects from patients and pays each **payee** its share of
every signed appointment: the professional's fee (``professional_price_cents``)
or the clinic's fee (``fee_cents``), both snapshotted when the appointment
was booked.

* An **advance** is money handed to the payee ahead of any settlement. It is
  deducted from the next settlement(s); what does not fit carries over.
* A **settlement** closes the account up to a cutoff date: every signed
  appointment not settled yet is gathered (``earned``), outstanding advances
  are applied (``advances``) and the **net** is split into ``n``
  installments with due dates (first due date + interval). A settlement
  whose net is zero needs no payment and is settled at once.
* Every **payment** (an advance, or an installment once paid) gets a
  sequential **receipt** number. The **professional signs** the receipt with
  its login to confirm the money was received; a clinic (no login) has its
  receipt **acknowledged** by the staff, recording who received the money.
* A settlement can be **voided** while nothing was paid: its appointments
  and advances are released.

Pure functions; the router loads rows and persists.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

PAYEE_PROFESSIONAL = "professional"
PAYEE_CLINIC = "clinic"
PAYEE_TYPES = (PAYEE_PROFESSIONAL, PAYEE_CLINIC)

KIND_ADVANCE = "advance"
KIND_INSTALLMENT = "installment"

METHODS = ("pix", "transfer", "cash", "check", "other")

STATUS_OPEN = "open"  # installments still unpaid
STATUS_AWAITING_SIGNATURE = "awaiting_signature"  # all paid, some receipts unsigned
STATUS_SETTLED = "settled"  # all paid and confirmed
STATUS_VOID = "void"

MAX_INSTALLMENTS = 12


class PayoutError(Exception):
    status_code = 409

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --- earnings -------------------------------------------------------------------------


def fee_for(row, payee_type: str) -> int:
    """What one signed appointment pays this kind of payee."""
    if row.status != "realizada":
        return 0
    return row.professional_price_cents if payee_type == PAYEE_PROFESSIONAL else row.fee_cents


def settlement_field(payee_type: str) -> str:
    return "professional_settlement_id" if payee_type == PAYEE_PROFESSIONAL else "clinic_settlement_id"


def is_settled(row, payee_type: str) -> bool:
    return getattr(row, settlement_field(payee_type)) is not None


def unsettled(rows: Iterable, payee_type: str, until: date | None = None) -> list:
    """Signed appointments not yet settled for this payee, up to ``until`` (inclusive)."""
    return [
        row
        for row in rows
        if row.status == "realizada"
        and not is_settled(row, payee_type)
        and (until is None or row.marked_at.date() <= until)
    ]


# --- advances -------------------------------------------------------------------------


def outstanding(advance) -> int:
    return max(0, advance.amount_cents - (advance.applied_cents or 0))


@dataclass
class Application:
    advance: object
    amount_cents: int


def apply_advances(advances: Iterable, earned_cents: int) -> list[Application]:
    """Deduct outstanding advances (oldest first) up to what was earned.
    The remainder of an advance carries over to the next settlement."""
    left = earned_cents
    result = []
    for advance in sorted(advances, key=lambda a: (a.paid_on or date.min, a.id)):
        if left <= 0:
            break
        available = outstanding(advance)
        if available <= 0:
            continue
        amount = min(available, left)
        result.append(Application(advance, amount))
        left -= amount
    return result


# --- installments ---------------------------------------------------------------------


def split_installments(net_cents: int, count: int, first_due_on: date, interval_days: int) -> list[tuple[int, int, date]]:
    """(sequence, amount, due date) for each installment. Equal parts, the
    cents that do not divide go to the first one. Zero net -> no installments."""
    if count < 1 or count > MAX_INSTALLMENTS:
        raise PayoutError(f"Installments must be between 1 and {MAX_INSTALLMENTS}", 400)
    if interval_days < 1:
        raise PayoutError("Interval must be at least one day", 400)
    if net_cents <= 0:
        return []
    base, rest = divmod(net_cents, count)
    return [
        (k + 1, base + (rest if k == 0 else 0), first_due_on + timedelta(days=interval_days * k))
        for k in range(count)
    ]


@dataclass
class SettlementPlan:
    appointments: list
    earned_cents: int
    applications: list[Application]
    advances_cents: int
    net_cents: int
    installments: list[tuple[int, int, date]]
    period_start: date
    period_end: date

    @property
    def appointments_count(self) -> int:
        return len(self.appointments)


def plan_settlement(
    appointments: Iterable,
    advances: Iterable,
    payee_type: str,
    until: date,
    count: int,
    first_due_on: date,
    interval_days: int,
) -> SettlementPlan:
    rows = sorted(unsettled(appointments, payee_type, until), key=lambda r: (r.marked_at, r.id))
    if not rows:
        raise PayoutError("Nothing to settle: no signed appointment up to that date is pending")
    earned = sum(fee_for(row, payee_type) for row in rows)
    applications = apply_advances(advances, earned)
    advances_cents = sum(item.amount_cents for item in applications)
    net = earned - advances_cents
    return SettlementPlan(
        appointments=rows,
        earned_cents=earned,
        applications=applications,
        advances_cents=advances_cents,
        net_cents=net,
        installments=split_installments(net, count, first_due_on, interval_days),
        period_start=rows[0].marked_at.date(),
        period_end=until,
    )


# --- statuses -------------------------------------------------------------------------


def is_confirmed(payment) -> bool:
    """The payee confirmed the money: the professional signed, or the staff
    acknowledged the clinic's receipt."""
    return payment.signed_at is not None or payment.acknowledged_at is not None


def settlement_status(settlement) -> str:
    if settlement.voided_at is not None:
        return STATUS_VOID
    payments = list(settlement.payments)
    if any(p.paid_on is None for p in payments):
        return STATUS_OPEN
    if any(not is_confirmed(p) for p in payments):
        return STATUS_AWAITING_SIGNATURE
    return STATUS_SETTLED


def can_void(settlement) -> bool:
    return settlement.voided_at is None and all(p.paid_on is None for p in settlement.payments)


def can_delete_advance(advance) -> bool:
    return (advance.applied_cents or 0) == 0 and not is_confirmed(advance)


# --- receipts -------------------------------------------------------------------------


def receipt_number(year: int, sequence: int) -> str:
    return f"REC-{year}-{sequence:06d}"


def next_receipt_number(existing: Iterable[str], paid_on: date) -> str:
    """Sequential within the year of the payment."""
    prefix = f"REC-{paid_on.year}-"
    used = [int(n[len(prefix) :]) for n in existing if n and n.startswith(prefix)]
    return receipt_number(paid_on.year, (max(used) + 1) if used else 1)


def fingerprint(payment, payee_name: str) -> str:
    """Integrity mark printed on the receipt: anyone holding the PDF can
    check it against the system (``GET /payouts/receipts/{id}``)."""
    confirmed_at = payment.signed_at or payment.acknowledged_at
    parts = [
        payment.receipt_number or "",
        payment.kind,
        payment.payee_type,
        payee_name,
        str(payment.amount_cents),
        payment.paid_on.isoformat() if payment.paid_on else "",
        payment.method or "",
        confirmed_at.isoformat(timespec="seconds") if confirmed_at else "",
        payment.received_by or "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16].upper()


# --- position -------------------------------------------------------------------------


@dataclass
class Position:
    """Where a payee stands right now."""

    unsettled_appointments: int = 0
    unsettled_cents: int = 0  # signed, not in any settlement
    advances_outstanding_cents: int = 0  # paid ahead, not deducted yet
    open_installments: int = 0
    open_installments_cents: int = 0  # settled, not paid yet
    receipts_to_confirm: int = 0
    receipts_to_confirm_cents: int = 0
    paid_cents: int = 0  # everything ever handed to the payee (advances + installments)
    settled_cents: int = 0  # earned amounts already closed in settlements

    @property
    def balance_cents(self) -> int:
        """What the payee would receive if settled today: unsettled earnings
        plus open installments minus outstanding advances."""
        return self.unsettled_cents + self.open_installments_cents - self.advances_outstanding_cents


def position(appointments: Iterable, payments: Iterable, settlements: Iterable, payee_type: str) -> Position:
    result = Position()
    for row in unsettled(appointments, payee_type):
        result.unsettled_appointments += 1
        result.unsettled_cents += fee_for(row, payee_type)
    for payment in payments:
        if payment.kind == KIND_ADVANCE:
            result.advances_outstanding_cents += outstanding(payment)
        if payment.paid_on is None:
            result.open_installments += 1
            result.open_installments_cents += payment.amount_cents
            continue
        result.paid_cents += payment.amount_cents
        if not is_confirmed(payment):
            result.receipts_to_confirm += 1
            result.receipts_to_confirm_cents += payment.amount_cents
    for settlement in settlements:
        if settlement.voided_at is None:
            result.settled_cents += settlement.earned_cents
    return result


# --- reports --------------------------------------------------------------------------


@dataclass
class PaymentTotals:
    advances: int = 0
    advances_cents: int = 0
    installments: int = 0
    installments_cents: int = 0
    unconfirmed: int = 0
    unconfirmed_cents: int = 0

    @property
    def paid_cents(self) -> int:
        return self.advances_cents + self.installments_cents

    def add(self, payment) -> None:
        if payment.paid_on is None:
            return
        if payment.kind == KIND_ADVANCE:
            self.advances += 1
            self.advances_cents += payment.amount_cents
        else:
            self.installments += 1
            self.installments_cents += payment.amount_cents
        if not is_confirmed(payment):
            self.unconfirmed += 1
            self.unconfirmed_cents += payment.amount_cents


def payment_totals(payments: Iterable, start: date | None = None, end: date | None = None) -> PaymentTotals:
    totals = PaymentTotals()
    for payment in payments:
        if payment.paid_on is None:
            continue
        if start is not None and payment.paid_on < start:
            continue
        if end is not None and payment.paid_on > end:
            continue
        totals.add(payment)
    return totals

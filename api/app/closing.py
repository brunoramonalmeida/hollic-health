"""Automatic monthly settlements.

On the closing day of each month (``SETTLEMENT_DAY``, default the 1st) the
worker closes the **previous month**: a ``PayoutClosing`` row is created
(unique per period, so it never runs twice) together with one outbox job
``payout.close``. That job fans out one ``payout.settle`` job per payee
(every professional and clinic), so a failure for one payee never blocks the
others and each can be retried on its own. Each ``payout.settle`` job is
idempotent: it skips payees already settled for that closing or with nothing
to settle, otherwise it creates the settlement with the standard plan:

* cutoff = the last day of the month;
* number of installments grows with the net amount: one more installment for
  every ``SETTLEMENT_INSTALLMENT_STEP_CENTS`` of net, capped at
  ``SETTLEMENT_MAX_INSTALLMENTS`` (net 0 → no installment, covered by advances);
* the first installment is due ``SETTLEMENT_FIRST_DUE_DAYS`` after the
  closing, the next ones every ``SETTLEMENT_INTERVAL_DAYS``.

Administrators may also trigger a closing by hand for a given period
(``POST /payouts/closings``), which goes through the very same jobs.
"""

import os
from datetime import date, timedelta

from sqlalchemy.orm import Session

from . import clock
from .models import Appointment, Clinic, PayoutClosing, PayoutPayment, PayoutSettlement, Professional
from .payouts import PAYEE_CLINIC, PAYEE_PROFESSIONAL, outstanding, unsettled
from .queue import enqueue

KIND_CLOSE = "payout.close"
KIND_SETTLE = "payout.settle"

SETTLEMENT_DAY = int(os.getenv("SETTLEMENT_DAY", "1"))
FIRST_DUE_DAYS = int(os.getenv("SETTLEMENT_FIRST_DUE_DAYS", "5"))
INTERVAL_DAYS = int(os.getenv("SETTLEMENT_INTERVAL_DAYS", "30"))
INSTALLMENT_STEP_CENTS = int(os.getenv("SETTLEMENT_INSTALLMENT_STEP_CENTS", "200000"))
MAX_INSTALLMENTS = int(os.getenv("SETTLEMENT_MAX_INSTALLMENTS", "3"))

STATUS_SCHEDULED = "scheduled"
STATUS_DISPATCHED = "dispatched"
STATUS_DONE = "done"


# --- pure rules -----------------------------------------------------------------------


def period_of(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    year, month = (int(part) for part in period.split("-"))
    start = date(year, month, 1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def previous_period(today: date) -> str:
    return period_of(today.replace(day=1) - timedelta(days=1))


def closing_due(today: date, closing_day: int = SETTLEMENT_DAY) -> str | None:
    """The period to close today, if the closing day was reached: always the previous month."""
    return previous_period(today) if today.day >= closing_day else None


def installments_for(net_cents: int, step_cents: int = INSTALLMENT_STEP_CENTS, maximum: int = MAX_INSTALLMENTS) -> int:
    """Installments grow with the amount: 1 + one per full ``step`` of net, capped."""
    if net_cents <= 0:
        return 1  # irrelevant: a zero net creates no installment
    return max(1, min(maximum, 1 + net_cents // step_cents))


# --- scheduling -----------------------------------------------------------------------


def schedule_closing(db: Session, period: str, *, triggered_by_id: int | None = None) -> PayoutClosing | None:
    """Create the closing of ``period`` and its job; None when it already exists."""
    if db.query(PayoutClosing).filter(PayoutClosing.period == period).first() is not None:
        return None
    _, period_end = period_bounds(period)
    closing = PayoutClosing(period=period, period_end=period_end, status=STATUS_SCHEDULED, triggered_by_id=triggered_by_id)
    db.add(closing)
    db.flush()
    enqueue(db, KIND_CLOSE, {"closing_id": closing.id, "period": period}, event=f"payout.close:{period}")
    return closing


def schedule_monthly_closing(db: Session, today: date | None = None) -> PayoutClosing | None:
    """Worker entry point: on/after the closing day, schedule the previous month once."""
    today = today or clock.today()
    period = closing_due(today)
    if period is None:
        return None
    closing = schedule_closing(db, period)
    if closing is not None:
        db.commit()
    return closing


# --- handlers (run by the worker through the queue) ---------------------------------------


def handle_close(db: Session, payload: dict) -> None:
    """Fan out: one ``payout.settle`` job per payee."""
    closing = db.get(PayoutClosing, payload["closing_id"])
    if closing is None:
        raise LookupError(f"Closing {payload.get('closing_id')} not found")
    if closing.status != STATUS_SCHEDULED:
        return  # duplicate delivery: already dispatched
    payees = [(PAYEE_PROFESSIONAL, row.id) for row in db.query(Professional.id).all()] + [(PAYEE_CLINIC, row.id) for row in db.query(Clinic.id).all()]
    for payee_type, payee_id in payees:
        enqueue(
            db,
            KIND_SETTLE,
            {"closing_id": closing.id, "payee_type": payee_type, "payee_id": payee_id},
            event=f"payout.settle:{closing.period}:{payee_type}:{payee_id}",
        )
    closing.payees_total = len(payees)
    closing.status = STATUS_DISPATCHED
    closing.dispatched_at = clock.now().replace(microsecond=0)
    db.commit()


def handle_settle(db: Session, payload: dict, create_settlement) -> None:
    """Settle one payee for the closing (idempotent). ``create_settlement``
    is the same routine the administrators use, injected to avoid a cycle."""
    closing = db.get(PayoutClosing, payload["closing_id"])
    if closing is None:
        raise LookupError(f"Closing {payload.get('closing_id')} not found")
    payee_type, payee_id = payload["payee_type"], payload["payee_id"]
    column = PayoutSettlement.professional_id if payee_type == PAYEE_PROFESSIONAL else PayoutSettlement.clinic_id
    already = (
        db.query(PayoutSettlement)
        .filter(PayoutSettlement.closing_id == closing.id, PayoutSettlement.payee_type == payee_type, column == payee_id)
        .first()
    )
    if already is not None:
        return  # duplicate delivery
    appointments_column = Appointment.professional_id if payee_type == PAYEE_PROFESSIONAL else Appointment.clinic_id
    signed = db.query(Appointment).filter(appointments_column == payee_id, Appointment.status == "realizada").all()
    pending = unsettled(signed, payee_type, closing.period_end)
    if not pending:
        closing.payees_skipped += 1
        _finish_if_complete(closing)
        db.commit()
        return
    payments_column = PayoutPayment.professional_id if payee_type == PAYEE_PROFESSIONAL else PayoutPayment.clinic_id
    advances = [
        p
        for p in db.query(PayoutPayment).filter(PayoutPayment.kind == "advance", PayoutPayment.payee_type == payee_type, payments_column == payee_id).all()
        if outstanding(p) > 0
    ]
    earned = sum((row.professional_price_cents if payee_type == PAYEE_PROFESSIONAL else row.fee_cents) for row in pending)
    net_estimate = max(0, earned - sum(outstanding(p) for p in advances))
    first_due = closing.period_end + timedelta(days=FIRST_DUE_DAYS)
    create_settlement(
        db,
        payee_type=payee_type,
        payee_id=payee_id,
        until=closing.period_end,
        installments=installments_for(net_estimate),
        first_due_on=first_due,
        interval_days=INTERVAL_DAYS,
        notes=f"Automatic monthly closing {closing.period}",
        closing_id=closing.id,
        actor_id=closing.triggered_by_id,
    )
    closing.settlements_created += 1
    _finish_if_complete(closing)
    db.commit()


def _finish_if_complete(closing: PayoutClosing) -> None:
    if closing.settlements_created + closing.payees_skipped >= closing.payees_total and closing.payees_total > 0:
        closing.status = STATUS_DONE
        closing.finished_at = clock.now().replace(microsecond=0)

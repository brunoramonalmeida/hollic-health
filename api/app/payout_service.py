"""Database-level payout operations shared by the API and the worker."""

from datetime import date

from sqlalchemy.orm import Session

from .models import AdvanceApplication, Appointment, Clinic, PayoutPayment, PayoutSettlement, Professional
from .notifications import payout_settlement_created
from .payouts import KIND_ADVANCE, KIND_INSTALLMENT, PAYEE_CLINIC, PAYEE_PROFESSIONAL, outstanding, plan_settlement, settlement_field


def payee_rows(db: Session, payee_type: str, payee_id: int):
    """Signed appointments, payments and settlements of a payee."""
    column = Appointment.professional_id if payee_type == PAYEE_PROFESSIONAL else Appointment.clinic_id
    appointments = db.query(Appointment).filter(column == payee_id, Appointment.status == "realizada").all()
    payments_column = PayoutPayment.professional_id if payee_type == PAYEE_PROFESSIONAL else PayoutPayment.clinic_id
    payments = db.query(PayoutPayment).filter(PayoutPayment.payee_type == payee_type, payments_column == payee_id).all()
    settlements_column = PayoutSettlement.professional_id if payee_type == PAYEE_PROFESSIONAL else PayoutSettlement.clinic_id
    settlements = db.query(PayoutSettlement).filter(PayoutSettlement.payee_type == payee_type, settlements_column == payee_id).all()
    return appointments, payments, settlements


def create_settlement(
    db: Session,
    *,
    payee_type: str,
    payee_id: int,
    until: date,
    installments: int,
    first_due_on: date,
    interval_days: int,
    notes: str = "",
    closing_id: int | None = None,
    actor_id: int | None = None,
) -> PayoutSettlement:
    """Plan and persist a settlement (raises ``PayoutError`` when there is nothing to settle)."""
    payee = db.get(Professional if payee_type == PAYEE_PROFESSIONAL else Clinic, payee_id)
    if payee is None:
        raise LookupError("Payee not found")
    appointments, payments, _ = payee_rows(db, payee_type, payee_id)
    advances = [p for p in payments if p.kind == KIND_ADVANCE and outstanding(p) > 0]
    plan = plan_settlement(appointments, advances, payee_type, until, installments, first_due_on, interval_days)
    settlement = PayoutSettlement(
        closing_id=closing_id,
        payee_type=payee_type,
        professional_id=payee_id if payee_type == PAYEE_PROFESSIONAL else None,
        clinic_id=payee_id if payee_type == PAYEE_CLINIC else None,
        period_start=plan.period_start,
        period_end=plan.period_end,
        appointments_count=plan.appointments_count,
        earned_cents=plan.earned_cents,
        advances_cents=plan.advances_cents,
        net_cents=plan.net_cents,
        installments_count=len(plan.installments),
        notes=notes,
        created_by_id=actor_id,
    )
    db.add(settlement)
    db.flush()
    field = settlement_field(payee_type)
    for row in plan.appointments:
        setattr(row, field, settlement.id)
    for item in plan.applications:
        item.advance.applied_cents = (item.advance.applied_cents or 0) + item.amount_cents
        db.add(AdvanceApplication(settlement_id=settlement.id, advance_id=item.advance.id, amount_cents=item.amount_cents))
    for sequence, amount, due_on in plan.installments:
        db.add(
            PayoutPayment(
                kind=KIND_INSTALLMENT,
                payee_type=payee_type,
                professional_id=settlement.professional_id,
                clinic_id=settlement.clinic_id,
                settlement_id=settlement.id,
                sequence=sequence,
                amount_cents=amount,
                due_on=due_on,
            )
        )
    db.flush()
    db.refresh(settlement)
    payout_settlement_created(db, settlement, payee.name, actor_id)
    return settlement

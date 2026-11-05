"""Applies the tax rules to services and stores the snapshot lines."""

from datetime import date

from sqlalchemy.orm import Session

from .models import (
    AgreementCharge,
    AgreementChargeTax,
    Appointment,
    AppointmentTax,
    Company,
    Installment,
    InstallmentTax,
    Subscription,
    TaxRule,
)
from .schemas import TaxLineOut
from .taxes import KIND_APPOINTMENT, KIND_SUBSCRIPTION, ServiceContext, compute_taxes, total


def line_out(line) -> TaxLineOut:
    """Works for computed ``TaxLine`` objects and stored tax rows alike."""
    return TaxLineOut(
        code=line.code,
        name=line.name,
        level=line.level,
        rate_bp=line.rate_bp,
        base_cents=line.base_cents,
        amount_cents=line.amount_cents,
        state=line.state or "",
        city=line.city or "",
        ibge_code=line.ibge_code or "",
    )


def get_company(db: Session) -> Company:
    row = db.get(Company, 1)
    if row is None:
        row = Company(id=1)
        db.add(row)
        db.flush()
    return row


def active_rules(db: Session) -> list[TaxRule]:
    return db.query(TaxRule).filter(TaxRule.active.is_(True)).all()


def appointment_context(row: Appointment) -> ServiceContext:
    clinic = row.clinic
    return ServiceContext(
        kind=KIND_APPOINTMENT,
        on=row.marked_at.date(),
        state=clinic.state or "",
        city=clinic.city or "",
        ibge_code=clinic.ibge_code or "",
        clinic_id=clinic.id,
        specialty_id=row.specialty_id,
        modality_id=row.modality_id,
    )


def apply_appointment_taxes(db: Session, row: Appointment) -> None:
    """Replace the appointment's tax lines with the rules in force on its date."""
    lines = compute_taxes(
        active_rules(db), appointment_context(row), amount_cents=row.amount_cents, gross_cents=row.gross_cents
    )
    row.taxes = [
        AppointmentTax(
            rule_id=line.rule_id,
            code=line.code,
            name=line.name,
            level=line.level,
            rate_bp=line.rate_bp,
            base_cents=line.base_cents,
            amount_cents=line.amount_cents,
            state=line.state,
            city=line.city,
            ibge_code=line.ibge_code,
        )
        for line in lines
    ]
    row.tax_cents = total(lines)
    row.net_cents = row.amount_cents - row.tax_cents


def installment_context(db: Session, subscription: Subscription, on: date) -> ServiceContext:
    company = get_company(db)
    return ServiceContext(
        kind=KIND_SUBSCRIPTION,
        on=on,
        state=company.state or "",
        city=company.city or "",
        ibge_code=company.ibge_code or "",
        modality_id=subscription.modality_id,
    )


def apply_installment_taxes(db: Session, subscription: Subscription, item: Installment) -> None:
    """Taxes on a plan fee, computed when the payment is registered."""
    if item.paid_on is None:
        clear_installment_taxes(item)
        return
    base = item.paid_cents if item.paid_cents is not None else item.amount_cents
    lines = compute_taxes(active_rules(db), installment_context(db, subscription, item.paid_on), amount_cents=base)
    item.taxes = [
        InstallmentTax(
            rule_id=line.rule_id,
            code=line.code,
            name=line.name,
            level=line.level,
            rate_bp=line.rate_bp,
            base_cents=line.base_cents,
            amount_cents=line.amount_cents,
            state=line.state,
            city=line.city,
            ibge_code=line.ibge_code,
        )
        for line in lines
    ]
    item.tax_cents = total(lines)


def clear_installment_taxes(item: Installment) -> None:
    item.taxes = []
    item.tax_cents = 0


def apply_charge_taxes(db: Session, charge: AgreementCharge) -> None:
    """Taxes on a corporate agreement's shortfall: plan-fee revenue billed at
    the head office, like the installments (no single plan: rules filtered
    by plan do not apply)."""
    company = get_company(db)
    context = ServiceContext(
        kind=KIND_SUBSCRIPTION,
        on=charge.paid_on,
        state=company.state or "",
        city=company.city or "",
        ibge_code=company.ibge_code or "",
    )
    lines = compute_taxes(active_rules(db), context, amount_cents=charge.amount_cents)
    charge.taxes = [
        AgreementChargeTax(
            rule_id=line.rule_id,
            code=line.code,
            name=line.name,
            level=line.level,
            rate_bp=line.rate_bp,
            base_cents=line.base_cents,
            amount_cents=line.amount_cents,
            state=line.state,
            city=line.city,
            ibge_code=line.ibge_code,
        )
        for line in lines
    ]
    charge.tax_cents = total(lines)

"""Paying professionals and clinics: advances, settlements with installments,
receipts (digital and PDF) and their confirmation.

* Administrators register advances, create settlements, pay installments,
  acknowledge clinic receipts and void unpaid settlements.
* A professional login sees its own position, advances, settlements and
  receipts, and **signs** each receipt to confirm the money was received.

Rules live in ``app.payouts``; the PDF in ``app.pdf``.
"""

import base64
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..addresses import format_address
from ..auth import ROLE_ADMIN, ROLE_PROFESSIONAL, get_current_user, is_professional, require_roles
from ..database import get_db
from ..models import AdvanceApplication, Appointment, Clinic, PayoutClosing, PayoutPayment, PayoutSettlement, Professional, User
from ..closing import STATUS_SCHEDULED, closing_due, period_bounds, schedule_closing
from ..notifications import payout_advance_registered, payout_installment_paid, payout_receipt_confirmed
from ..payout_service import create_settlement, payee_rows
from ..payouts import (
    KIND_ADVANCE,
    KIND_INSTALLMENT,
    METHODS,
    PAYEE_CLINIC,
    PAYEE_PROFESSIONAL,
    STATUS_VOID,
    PayoutError,
    can_delete_advance,
    can_void,
    fee_for,
    fingerprint,
    is_confirmed,
    next_receipt_number,
    outstanding,
    position,
    settlement_field,
    settlement_status,
)
from ..pdf import Document
from ..schemas import (
    AcknowledgeIn,
    AdvanceApplicationOut,
    AdvanceIn,
    ClosingIn,
    ClosingOut,
    InstallmentPaymentIn,
    PayeeOut,
    PaymentOut,
    PositionOut,
    ReceiptOut,
    SettlementAppointmentOut,
    SettlementIn,
    SettlementOut,
    SignBody,
)
from ..signing import SigningError, validate_signature_image, verify_credentials
from ..taxation import get_company

router = APIRouter(prefix="/payouts", tags=["payouts"], dependencies=[Depends(require_roles(ROLE_ADMIN, ROLE_PROFESSIONAL))])

ADMIN = [Depends(require_roles(ROLE_ADMIN))]


# --- payees ---------------------------------------------------------------------------


def _load_payee(db: Session, payee_type: str, payee_id: int):
    if payee_type == PAYEE_PROFESSIONAL:
        row = (
            db.query(Professional)
            .options(joinedload(Professional.specialty), joinedload(Professional.clinic))
            .filter(Professional.id == payee_id)
            .first()
        )
    elif payee_type == PAYEE_CLINIC:
        row = db.get(Clinic, payee_id)
    else:
        raise HTTPException(status_code=400, detail="payee_type must be professional or clinic")
    if row is None:
        raise HTTPException(status_code=404, detail="Payee not found")
    return row


def _payee_out(db: Session, payee_type: str, row) -> PayeeOut:
    if payee_type == PAYEE_PROFESSIONAL:
        has_login = db.query(User).filter(User.role == ROLE_PROFESSIONAL, User.professional_id == row.id).count() > 0
        return PayeeOut(payee_type=payee_type, payee_id=row.id, name=row.name, detail=f"{row.specialty.name} · {row.clinic.name}", has_login=has_login)
    return PayeeOut(payee_type=payee_type, payee_id=row.id, name=row.name, detail=f"{row.city}{'/' + row.state if row.state else ''}", has_login=False)


def _payee_of(obj):
    """(payee_type, payee_id, payee row) of a payment or settlement."""
    if obj.payee_type == PAYEE_PROFESSIONAL:
        return obj.payee_type, obj.professional_id, obj.professional
    return obj.payee_type, obj.clinic_id, obj.clinic


def _scope(user: User, payee_type: str, payee_id: int) -> None:
    """A professional login only sees its own account."""
    if is_professional(user) and not (payee_type == PAYEE_PROFESSIONAL and payee_id == user.professional_id):
        raise HTTPException(status_code=403, detail="Not allowed for another payee")


def _own_filter(user: User, query, model):
    if is_professional(user):
        return query.filter(model.payee_type == PAYEE_PROFESSIONAL, model.professional_id == user.professional_id)
    return query


def _payee_filter(query, model, payee_type: str | None, payee_id: int | None):
    if payee_type is None:
        return query
    if payee_id is None:
        raise HTTPException(status_code=400, detail="payee_id is required with payee_type")
    if payee_type == PAYEE_PROFESSIONAL:
        return query.filter(model.payee_type == payee_type, model.professional_id == payee_id)
    return query.filter(model.payee_type == payee_type, model.clinic_id == payee_id)


# --- loaders / serializers ---------------------------------------------------------------


def _payments_query(db: Session):
    return db.query(PayoutPayment).options(
        joinedload(PayoutPayment.professional).joinedload(Professional.specialty),
        joinedload(PayoutPayment.professional).joinedload(Professional.clinic),
        joinedload(PayoutPayment.clinic),
        joinedload(PayoutPayment.registered_by),
        joinedload(PayoutPayment.signed_by),
        joinedload(PayoutPayment.acknowledged_by),
        joinedload(PayoutPayment.settlement),
    )


def _settlements_query(db: Session):
    return db.query(PayoutSettlement).options(
        joinedload(PayoutSettlement.professional).joinedload(Professional.specialty),
        joinedload(PayoutSettlement.professional).joinedload(Professional.clinic),
        joinedload(PayoutSettlement.clinic),
        joinedload(PayoutSettlement.created_by),
        selectinload(PayoutSettlement.payments).joinedload(PayoutPayment.registered_by),
        selectinload(PayoutSettlement.payments).joinedload(PayoutPayment.signed_by),
        selectinload(PayoutSettlement.payments).joinedload(PayoutPayment.acknowledged_by),
        selectinload(PayoutSettlement.applications).joinedload(AdvanceApplication.advance),
    )


def _can_sign(user: User, payment: PayoutPayment) -> bool:
    return (
        is_professional(user)
        and payment.payee_type == PAYEE_PROFESSIONAL
        and payment.professional_id == user.professional_id
        and payment.paid_on is not None
        and not is_confirmed(payment)
    )


def _can_acknowledge(user: User, payment: PayoutPayment) -> bool:
    return user.role == ROLE_ADMIN and payment.payee_type == PAYEE_CLINIC and payment.paid_on is not None and not is_confirmed(payment)


def _payment_out(db: Session, payment: PayoutPayment, user: User) -> PaymentOut:
    payee_type, payee_id, payee = _payee_of(payment)
    return PaymentOut(
        id=payment.id,
        kind=payment.kind,
        payee=_payee_out(db, payee_type, payee),
        settlement_id=payment.settlement_id,
        sequence=payment.sequence,
        installments_count=payment.settlement.installments_count if payment.settlement is not None else None,
        amount_cents=payment.amount_cents,
        due_on=payment.due_on,
        paid_on=payment.paid_on,
        method=payment.method or "",
        reference=payment.reference or "",
        notes=payment.notes or "",
        applied_cents=payment.applied_cents or 0,
        outstanding_cents=outstanding(payment) if payment.kind == KIND_ADVANCE else 0,
        receipt_number=payment.receipt_number,
        registered_by_name=payment.registered_by.name if payment.registered_by else None,
        registered_at=payment.registered_at,
        signed_at=payment.signed_at,
        signed_by_name=payment.signed_by.name if payment.signed_by else None,
        signature_image=payment.signature_image,
        acknowledged_at=payment.acknowledged_at,
        acknowledged_by_name=payment.acknowledged_by.name if payment.acknowledged_by else None,
        received_by=payment.received_by or "",
        received_by_document=payment.received_by_document or "",
        confirmed=is_confirmed(payment),
        can_sign=_can_sign(user, payment),
        can_acknowledge=_can_acknowledge(user, payment),
        can_delete=user.role == ROLE_ADMIN and payment.kind == KIND_ADVANCE and can_delete_advance(payment),
        fingerprint=fingerprint(payment, payee.name) if payment.paid_on is not None else None,
    )


def _settlement_appointments(db: Session, settlement: PayoutSettlement) -> list[SettlementAppointmentOut]:
    column = getattr(Appointment, settlement_field(settlement.payee_type))
    rows = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.customer),
            joinedload(Appointment.dependent),
            joinedload(Appointment.professional),
            joinedload(Appointment.clinic),
        )
        .filter(column == settlement.id)
        .order_by(Appointment.marked_at)
        .all()
    )
    return [
        SettlementAppointmentOut(
            appointment_id=row.id,
            marked_at=row.marked_at,
            patient=row.dependent.name if row.dependent else row.customer.name,
            professional_name=row.professional.name,
            clinic_name=row.clinic.name,
            fee_cents=fee_for(row, settlement.payee_type),
        )
        for row in rows
    ]


def _settlement_out(db: Session, settlement: PayoutSettlement, user: User, with_appointments: bool = False) -> SettlementOut:
    payee_type, payee_id, payee = _payee_of(settlement)
    payments = list(settlement.payments)
    return SettlementOut(
        id=settlement.id,
        closing_id=settlement.closing_id,
        payee=_payee_out(db, payee_type, payee),
        status=settlement_status(settlement),
        period_start=settlement.period_start,
        period_end=settlement.period_end,
        appointments_count=settlement.appointments_count,
        earned_cents=settlement.earned_cents,
        advances_cents=settlement.advances_cents,
        net_cents=settlement.net_cents,
        installments_count=settlement.installments_count,
        paid_cents=sum(p.amount_cents for p in payments if p.paid_on is not None),
        confirmed_cents=sum(p.amount_cents for p in payments if is_confirmed(p)),
        notes=settlement.notes or "",
        created_by_name=settlement.created_by.name if settlement.created_by else None,
        created_at=settlement.created_at,
        voided_at=settlement.voided_at,
        can_void=user.role == ROLE_ADMIN and can_void(settlement),
        installments=[_payment_out(db, p, user) for p in payments],
        advances_applied=[
            AdvanceApplicationOut(
                advance_id=item.advance_id,
                receipt_number=item.advance.receipt_number,
                paid_on=item.advance.paid_on,
                amount_cents=item.amount_cents,
            )
            for item in settlement.applications
        ],
        appointments=_settlement_appointments(db, settlement) if with_appointments else [],
    )


_payee_rows = payee_rows


def _issue_receipt(db: Session, payment: PayoutPayment, paid_on: date) -> None:
    existing = [n for (n,) in db.query(PayoutPayment.receipt_number).filter(PayoutPayment.receipt_number.isnot(None)).all()]
    payment.receipt_number = next_receipt_number(existing, paid_on)


def _check_method(method: str) -> str:
    if method not in METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {', '.join(METHODS)}")
    return method


# --- position -------------------------------------------------------------------------


@router.get("/payees", response_model=list[PayeeOut], dependencies=ADMIN)
def list_payees(db: Session = Depends(get_db)):
    """Everyone who can be paid: professionals and clinics."""
    professionals = db.query(Professional).options(joinedload(Professional.specialty), joinedload(Professional.clinic)).order_by(Professional.name).all()
    clinics = db.query(Clinic).order_by(Clinic.name).all()
    return [_payee_out(db, PAYEE_PROFESSIONAL, row) for row in professionals] + [_payee_out(db, PAYEE_CLINIC, row) for row in clinics]


@router.get("/position", response_model=PositionOut)
def payee_position(
    payee_type: str = Query(default=PAYEE_PROFESSIONAL),
    payee_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Where the payee stands: earned and not settled, advances outstanding,
    installments to receive, receipts to confirm, balance."""
    if is_professional(user):
        payee_type, payee_id = PAYEE_PROFESSIONAL, user.professional_id
    if payee_id is None:
        raise HTTPException(status_code=400, detail="payee_id is required")
    _scope(user, payee_type, payee_id)
    payee = _load_payee(db, payee_type, payee_id)
    appointments, payments, settlements = _payee_rows(db, payee_type, payee_id)
    pos = position(appointments, payments, settlements, payee_type)
    return PositionOut(payee=_payee_out(db, payee_type, payee), **{k: getattr(pos, k) for k in PositionOut.model_fields if k not in ("payee", "balance_cents")}, balance_cents=pos.balance_cents)


# --- advances -------------------------------------------------------------------------


@router.get("/advances", response_model=list[PaymentOut])
def list_advances(
    payee_type: str | None = None,
    payee_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = _own_filter(user, _payments_query(db).filter(PayoutPayment.kind == KIND_ADVANCE), PayoutPayment)
    query = _payee_filter(query, PayoutPayment, payee_type, payee_id)
    return [_payment_out(db, row, user) for row in query.order_by(PayoutPayment.paid_on.desc(), PayoutPayment.id.desc()).all()]


@router.post("/advances", response_model=PaymentOut, status_code=201, dependencies=ADMIN)
def register_advance(body: AdvanceIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Money handed to the payee ahead of the settlement. A receipt is issued
    at once; the professional must sign it."""
    payee = _load_payee(db, body.payee_type, body.payee_id)
    row = PayoutPayment(
        kind=KIND_ADVANCE,
        payee_type=body.payee_type,
        professional_id=payee.id if body.payee_type == PAYEE_PROFESSIONAL else None,
        clinic_id=payee.id if body.payee_type == PAYEE_CLINIC else None,
        amount_cents=body.amount_cents,
        paid_on=body.paid_on,
        method=_check_method(body.method),
        reference=body.reference,
        notes=body.notes,
        registered_by_id=user.id,
        registered_at=clock.now().replace(microsecond=0),
    )
    db.add(row)
    db.flush()
    _issue_receipt(db, row, body.paid_on)
    db.flush()
    saved = _payments_query(db).filter(PayoutPayment.id == row.id).one()
    payout_advance_registered(db, saved, payee.name, user)
    db.commit()
    return _payment_out(db, _payments_query(db).filter(PayoutPayment.id == row.id).one(), user)


@router.delete("/advances/{item_id}", status_code=204, dependencies=ADMIN)
def delete_advance(item_id: int, db: Session = Depends(get_db)):
    """Remove an advance registered by mistake: only while it was neither
    deducted by a settlement nor confirmed by the payee."""
    row = db.get(PayoutPayment, item_id)
    if row is None or row.kind != KIND_ADVANCE:
        raise HTTPException(status_code=404, detail="Advance not found")
    if not can_delete_advance(row):
        raise HTTPException(status_code=409, detail="Advance already deducted or confirmed; it cannot be removed")
    db.delete(row)
    db.commit()


# --- settlements ----------------------------------------------------------------------


@router.get("/settlements", response_model=list[SettlementOut])
def list_settlements(
    payee_type: str | None = None,
    payee_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = _own_filter(user, _settlements_query(db), PayoutSettlement)
    query = _payee_filter(query, PayoutSettlement, payee_type, payee_id)
    return [_settlement_out(db, row, user) for row in query.order_by(PayoutSettlement.id.desc()).all()]


@router.post("/settlements", response_model=SettlementOut, status_code=201, dependencies=ADMIN)
def create_settlement_endpoint(body: SettlementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Close the payee's account up to ``until``: gather the signed
    appointments not settled yet, deduct outstanding advances and split the
    net into installments. (The monthly closing does the same automatically.)"""
    _load_payee(db, body.payee_type, body.payee_id)
    try:
        settlement = create_settlement(
            db,
            payee_type=body.payee_type,
            payee_id=body.payee_id,
            until=body.until,
            installments=body.installments,
            first_due_on=body.first_due_on,
            interval_days=body.interval_days,
            notes=body.notes,
            actor_id=user.id,
        )
    except PayoutError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    return _settlement_out(db, _settlements_query(db).filter(PayoutSettlement.id == settlement.id).one(), user, with_appointments=True)


def _load_settlement(db: Session, item_id: int, user: User) -> PayoutSettlement:
    row = _settlements_query(db).filter(PayoutSettlement.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Settlement not found")
    payee_type, payee_id, _ = _payee_of(row)
    _scope(user, payee_type, payee_id)
    return row


@router.get("/settlements/{item_id}", response_model=SettlementOut)
def get_settlement(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _settlement_out(db, _load_settlement(db, item_id, user), user, with_appointments=True)


@router.post("/settlements/{item_id}/void", response_model=SettlementOut, dependencies=ADMIN)
def void_settlement(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Cancel a settlement nothing was paid on: its appointments and advances are released."""
    row = _load_settlement(db, item_id, user)
    if not can_void(row):
        raise HTTPException(status_code=409, detail="Settlement already has payments (or is void)")
    field = settlement_field(row.payee_type)
    for appointment in db.query(Appointment).filter(getattr(Appointment, field) == row.id).all():
        setattr(appointment, field, None)
    for item in row.applications:
        item.advance.applied_cents = max(0, (item.advance.applied_cents or 0) - item.amount_cents)
    for payment in list(row.payments):
        db.delete(payment)
    row.voided_at = clock.now().replace(microsecond=0)
    row.voided_by_id = user.id
    db.commit()
    return _settlement_out(db, _load_settlement(db, item_id, user), user)


@router.put("/settlements/{item_id}/installments/{payment_id}", response_model=SettlementOut, dependencies=ADMIN)
def pay_installment(
    item_id: int,
    payment_id: int,
    body: InstallmentPaymentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register the payment of one installment: a receipt is issued for the payee to confirm."""
    settlement = _load_settlement(db, item_id, user)
    if settlement.voided_at is not None:
        raise HTTPException(status_code=409, detail="Settlement is void")
    payment = next((p for p in settlement.payments if p.id == payment_id), None)
    if payment is None:
        raise HTTPException(status_code=404, detail="Installment not found")
    if payment.paid_on is not None:
        raise HTTPException(status_code=409, detail=f"Installment already paid on {payment.paid_on:%d/%m/%Y}")
    payment.paid_on = body.paid_on
    payment.method = _check_method(body.method)
    payment.reference = body.reference
    payment.notes = body.notes
    payment.registered_by_id = user.id
    payment.registered_at = clock.now().replace(microsecond=0)
    _issue_receipt(db, payment, body.paid_on)
    db.flush()
    payee_name = _payee_of(settlement)[2].name
    payout_installment_paid(db, _payments_query(db).filter(PayoutPayment.id == payment.id).one(), payee_name, user)
    db.commit()
    return _settlement_out(db, _load_settlement(db, item_id, user), user)


# --- receipts -------------------------------------------------------------------------


@router.get("/receipts", response_model=list[PaymentOut])
def list_receipts(
    payee_type: str | None = None,
    payee_id: int | None = None,
    unconfirmed: bool = False,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every payment made (advances and paid installments), newest first."""
    query = _own_filter(user, _payments_query(db).filter(PayoutPayment.paid_on.isnot(None)), PayoutPayment)
    query = _payee_filter(query, PayoutPayment, payee_type, payee_id)
    if start is not None:
        query = query.filter(PayoutPayment.paid_on >= start)
    if end is not None:
        query = query.filter(PayoutPayment.paid_on <= end)
    rows = query.order_by(PayoutPayment.paid_on.desc(), PayoutPayment.id.desc()).all()
    if unconfirmed:
        rows = [row for row in rows if not is_confirmed(row)]
    return [_payment_out(db, row, user) for row in rows]


def _load_payment(db: Session, item_id: int, user: User) -> PayoutPayment:
    row = _payments_query(db).filter(PayoutPayment.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    payee_type, payee_id, _ = _payee_of(row)
    _scope(user, payee_type, payee_id)
    return row


def _receipt_texts(payment: PayoutPayment, payee_name: str) -> tuple[str, str]:
    if payment.kind == KIND_ADVANCE:
        return "Advance receipt", f"Advance paid to {payee_name} on {payment.paid_on:%d/%m/%Y}, to be deducted from the next settlement(s)."
    settlement = payment.settlement
    return (
        "Payout receipt",
        f"Installment {payment.sequence} of {settlement.installments_count} of the settlement of {payee_name} "
        f"for the period {settlement.period_start:%d/%m/%Y} – {settlement.period_end:%d/%m/%Y} "
        f"({settlement.appointments_count} signed appointments).",
    )


def _receipt_out(db: Session, payment: PayoutPayment, user: User) -> ReceiptOut:
    if payment.paid_on is None:
        raise HTTPException(status_code=409, detail="No receipt yet: the installment was not paid")
    payee_name = _payee_of(payment)[2].name
    company = get_company(db)
    title, description = _receipt_texts(payment, payee_name)
    settlement = None
    if payment.settlement_id is not None:
        settlement = _settlement_out(db, _settlements_query(db).filter(PayoutSettlement.id == payment.settlement_id).one(), user)
    return ReceiptOut(
        payment=_payment_out(db, payment, user),
        company_name=company.name,
        company_cnpj=company.cnpj or "",
        company_address=format_address(company),
        title=title,
        description=description,
        settlement=settlement,
        fingerprint=fingerprint(payment, payee_name),
        verify_path=f"/payouts/receipts/{payment.id}",
    )


def money(cents: int) -> str:
    units, rest = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    return f"{sign}R$ {units:,}".replace(",", ".") + f",{rest:02d}"


def _receipt_pdf(receipt: ReceiptOut) -> bytes:
    payment = receipt.payment
    doc = Document()
    doc.text(receipt.company_name, size=16, bold=True)
    if receipt.company_cnpj:
        doc.text(f"CNPJ {receipt.company_cnpj}", size=9)
    if receipt.company_address:
        doc.text(receipt.company_address, size=9)
    doc.gap()
    doc.rule()
    doc.columns([(receipt.title.upper(), 0), (f"No. {payment.receipt_number}", 330)], size=13, bold=True)
    doc.rule()
    doc.gap()
    doc.columns([("Paid to", 0), (payment.payee.name, 120)], bold=False)
    doc.columns([("", 0), (payment.payee.detail, 120)], size=9)
    doc.columns([("Type", 0), ("Professional" if payment.payee.payee_type == "professional" else "Clinic", 120)])
    doc.columns([("Amount", 0), (money(payment.amount_cents), 120)], size=13, bold=True)
    doc.columns([("Date", 0), (payment.paid_on.strftime("%d/%m/%Y"), 120)])
    doc.columns([("Method", 0), (f"{payment.method}{' · ' + payment.reference if payment.reference else ''}", 120)])
    doc.columns([("Registered by", 0), (f"{payment.registered_by_name or '-'} on {payment.registered_at:%d/%m/%Y %H:%M}" if payment.registered_at else payment.registered_by_name or "-", 120)])
    doc.gap()
    doc.text(receipt.description, size=10)
    if payment.notes:
        doc.text(f"Notes: {payment.notes}", size=9)
    settlement = receipt.settlement
    if settlement is not None:
        doc.gap()
        doc.text("Settlement", size=11, bold=True)
        doc.columns([("Signed appointments", 0), (str(settlement.appointments_count), 160)])
        doc.columns([("Earned", 0), (money(settlement.earned_cents), 160)])
        doc.columns([("Advances deducted", 0), (money(settlement.advances_cents), 160)])
        doc.columns([("Net to pay", 0), (money(settlement.net_cents), 160)], bold=True)
        doc.gap()
        doc.columns([("Installment", 0), ("Due", 90), ("Paid", 170), ("Amount", 250), ("Receipt", 330)], size=9, bold=True)
        for item in settlement.installments:
            doc.columns(
                [
                    (f"{item.sequence} of {settlement.installments_count}", 0),
                    (item.due_on.strftime("%d/%m/%Y") if item.due_on else "-", 90),
                    (item.paid_on.strftime("%d/%m/%Y") if item.paid_on else "-", 170),
                    (money(item.amount_cents), 250),
                    (item.receipt_number or "-", 330),
                ],
                size=9,
            )
        for applied in settlement.advances_applied:
            doc.columns([(f"Advance {applied.receipt_number or applied.advance_id}", 0), ((applied.paid_on.strftime("%d/%m/%Y") if applied.paid_on else "-"), 90), ("deducted", 170), (money(-applied.amount_cents), 250)], size=9)
    doc.gap()
    doc.rule()
    doc.text("Confirmation of receipt", size=11, bold=True)
    if payment.signed_at:
        doc.text(f"Digitally signed by {payment.signed_by_name} on {payment.signed_at:%d/%m/%Y %H:%M}, confirming the amount above was received.")
        doc.text("Password and authenticator code verified; hand-drawn signature:", size=9)
        if payment.signature_image:
            try:
                doc.image(base64.b64decode(payment.signature_image.split(",", 1)[1]), width=220)
            except (ValueError, IndexError):
                doc.text("(signature drawing unavailable)", size=9)
    elif payment.acknowledged_at:
        doc.text(f"Received by {payment.received_by}{' (' + payment.received_by_document + ')' if payment.received_by_document else ''}, "
                 f"recorded by {payment.acknowledged_by_name} on {payment.acknowledged_at:%d/%m/%Y %H:%M}.")
    else:
        doc.text("NOT CONFIRMED YET: the payee has not signed this receipt.", bold=True)
    doc.gap()
    doc.text(f"Fingerprint {receipt.fingerprint} · verify at {receipt.verify_path}", size=8)
    doc.text(f"Issued on {clock.now():%d/%m/%Y %H:%M}", size=8)
    return doc.render()


@router.get("/receipts/{item_id}.pdf")
def receipt_pdf(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The receipt as a PDF file."""
    receipt = _receipt_out(db, _load_payment(db, item_id, user), user)
    filename = f"receipt_{receipt.payment.receipt_number}.pdf"
    return Response(
        content=_receipt_pdf(receipt),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Declared after the ".pdf" route so that "5.pdf" is not taken for an id.
@router.get("/receipts/{item_id}", response_model=ReceiptOut)
def get_receipt(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The digital receipt."""
    return _receipt_out(db, _load_payment(db, item_id, user), user)


@router.post("/receipts/{item_id}/sign", response_model=ReceiptOut)
def sign_receipt(item_id: int, body: SignBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The professional confirms the money was received: password,
    authenticator code and hand-drawn signature (see app.signing)."""
    payment = _load_payment(db, item_id, user)
    if not is_professional(user):
        raise HTTPException(status_code=403, detail="Only the professional signs its receipts")
    if payment.paid_on is None:
        raise HTTPException(status_code=409, detail="Nothing to sign: the installment was not paid")
    if is_confirmed(payment):
        raise HTTPException(status_code=409, detail="Receipt already confirmed")
    now = clock.now()
    try:
        validate_signature_image(body.signature_image)
        verify_credentials(user, body.password, body.code, now)
    except SigningError as exc:
        db.commit()  # persist the failure count / lock
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    payment.signed_at = now.replace(microsecond=0)
    payment.signed_by_id = user.id
    payment.signature_image = body.signature_image
    db.flush()
    payout_receipt_confirmed(db, _payments_query(db).filter(PayoutPayment.id == payment.id).one(), user)
    db.commit()
    return _receipt_out(db, _load_payment(db, item_id, user), user)


@router.post("/receipts/{item_id}/acknowledge", response_model=ReceiptOut, dependencies=ADMIN)
def acknowledge_receipt(item_id: int, body: AcknowledgeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Clinic payees have no login: the staff records who received the money."""
    payment = _load_payment(db, item_id, user)
    if payment.payee_type != PAYEE_CLINIC:
        raise HTTPException(status_code=409, detail="Professionals confirm their receipts with their own login")
    if payment.paid_on is None:
        raise HTTPException(status_code=409, detail="Nothing to acknowledge: the installment was not paid")
    if is_confirmed(payment):
        raise HTTPException(status_code=409, detail="Receipt already confirmed")
    payment.acknowledged_at = clock.now().replace(microsecond=0)
    payment.acknowledged_by_id = user.id
    payment.received_by = body.received_by
    payment.received_by_document = body.received_by_document
    db.commit()
    return _receipt_out(db, _load_payment(db, item_id, user), user)


# --- monthly closings -----------------------------------------------------------------


def _closing_out(row: PayoutClosing) -> ClosingOut:
    start, _ = period_bounds(row.period)
    return ClosingOut(
        id=row.id,
        period=row.period,
        period_start=start,
        period_end=row.period_end,
        status=row.status,
        payees_total=row.payees_total,
        settlements_created=row.settlements_created,
        payees_skipped=row.payees_skipped,
        automatic=row.triggered_by_id is None,
        triggered_by_name=row.triggered_by.name if row.triggered_by else None,
        created_at=row.created_at,
        dispatched_at=row.dispatched_at,
        finished_at=row.finished_at,
    )


@router.get("/closings", response_model=list[ClosingOut], dependencies=ADMIN)
def list_closings(db: Session = Depends(get_db)):
    """Monthly closings, newest first (automatic on the closing day, or triggered by hand)."""
    rows = db.query(PayoutClosing).options(joinedload(PayoutClosing.triggered_by)).order_by(PayoutClosing.period.desc()).all()
    return [_closing_out(row) for row in rows]


@router.post("/closings", response_model=ClosingOut, status_code=201, dependencies=ADMIN)
def trigger_closing(body: ClosingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Close a month by hand (e.g. before the closing day). Goes through the
    same queue jobs as the automatic closing; a period is closed only once."""
    try:
        _, period_end = period_bounds(body.period)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    if period_end >= clock.today() and body.period == clock.today().strftime("%Y-%m"):
        raise HTTPException(status_code=400, detail="The current month can only be closed after it ends")
    closing = schedule_closing(db, body.period, triggered_by_id=user.id)
    if closing is None:
        raise HTTPException(status_code=409, detail=f"Period {body.period} was already closed")
    db.commit()
    return _closing_out(db.query(PayoutClosing).options(joinedload(PayoutClosing.triggered_by)).filter(PayoutClosing.id == closing.id).one())

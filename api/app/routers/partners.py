"""Partner companies (B2B), their six-month agreements and the company's
monthly invoices (the staff side; the companies' own portal is
``app.routers.portal``).

The staff reads partners and agreements (the reception enrolls employees
on the subscription form) and registers the company's payments; only
administrators and supervisors write partners and agreements, because the
negotiated discounts move money, like the price list and the sales regions.
The rules of an agreement live in ``app.partners``.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..auth import get_current_user, require_pricing, require_staff
from ..categories import plan_grace_days
from ..billing import PART_COMPANY, installment_status, monthly_amount, part_cents, register_part, subscription_status
from ..database import get_db
from ..models import Agreement, AgreementCharge, AgreementPlan, Modality, Partner, Subscription, User
from ..copayment import company_statement, company_statement_csv, default_range, record_share, share_effective_from
from ..notifications import agreement_invoice_paid, agreement_minimum_changed, installment_paid, installment_part_paid
from ..partners import (
    AGREEMENT_IN_FORCE,
    AGREEMENT_STATUSES,
    Ledger,
    agreement_query,
    agreement_status,
    contract_end,
    contract_share,
    has_enrollments,
    invoice_totals,
    ledger,
    ledgers,
    load_agreement,
    parse_period,
    partner_query,
    reference_period,
    refresh_minimum,
    share_on,
    terms_of,
)
from ..schemas import (
    AgreementIn,
    AgreementOut,
    AgreementPlanOut,
    AgreementRenewIn,
    CompanyStatementOut,
    InvoiceDetailOut,
    InvoiceLineOut,
    InvoiceOut,
    InvoicePay,
    PartnerIn,
    PartnerOut,
    ShareChangeIn,
    ShareChangeOut,
)
from ..taxation import apply_charge_taxes, apply_installment_taxes

WRITE = [Depends(require_pricing)]

partners = APIRouter(prefix="/partners", tags=["partners"], dependencies=[Depends(require_staff)])
agreements = APIRouter(prefix="/agreements", tags=["partners"], dependencies=[Depends(require_staff)])


# --- shared with the portal --------------------------------------------------------------


def track_minimum(db: Session, agreement_ids, actor: User | None) -> None:
    """After the enrollments of agreements changed: record whether each one
    meets its minimum this month, and tell the staff and the company when
    that flipped. (The employees' fees never change: the terms are frozen.)"""
    today = clock.today()
    for agreement_id in dict.fromkeys(item for item in agreement_ids if item is not None):
        db.flush()
        agreement = load_agreement(db, agreement_id)
        if agreement is None:
            continue
        state = refresh_minimum(db, agreement, ledger(db, agreement_id), today)
        if state.changed:
            agreement_minimum_changed(db, agreement, state, actor)


def _charges(row: Agreement) -> dict:
    return {charge.period: charge for charge in row.charges}


def _totals(row: Agreement, book: Ledger, period: str, today: date):
    return invoice_totals(period, book.lines(period), today, row.min_employees, row.payer, _charges(row).get(period))


def agreement_out(row: Agreement, book: Ledger, today: date | None = None) -> AgreementOut:
    today = today or clock.today()
    period = reference_period(row, today)
    current = _totals(row, book, period, today)
    minimum = max(row.min_employees or 1, 1)
    open_shortfall = sum(
        totals.shortfall_cents for totals in (_totals(row, book, item, today) for item in book.periods()) if totals.shortfall_payable
    )
    plans = sorted(row.plans, key=lambda item: item.modality.name.lower())
    return AgreementOut(
        id=row.id,
        partner_id=row.partner_id,
        partner_name=row.partner.name,
        partner_active=bool(row.partner.active),
        name=row.name,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        min_employees=minimum,
        payer=row.payer,
        company_share_percent=contract_share(row),
        active=bool(row.active),
        notes=row.notes or "",
        status=agreement_status(row, today),
        terms_frozen=bool(book.enrollments),
        plans=[
            AgreementPlanOut(
                modality_id=item.modality_id,
                discount_percent=item.discount_percent,
                grace_days=item.grace_days,
                company_share_percent=item.company_share_percent,
                modality_name=item.modality.name,
                plan_value_cents=item.modality.plan_value_cents,
                value_cents=monthly_amount(item.modality, 0, item.discount_percent),
                plan_grace_days=plan_grace_days(item.modality),
            )
            for item in plans
        ],
        reference_period=period,
        employees=current.employees,
        conditions_met=current.employees >= minimum,
        missing_employees=max(minimum - current.employees, 0),
        shortfall_cents=current.shortfall_cents,
        shortfall_open_cents=open_shortfall,
        enrollments_count=sum(1 for item in book.enrollments if item.cancelled_on is None),
        created_at=row.created_at,
    )


def load_or_404(db: Session, item_id: int, partner_id: int | None = None) -> Agreement:
    """The agreement (of ``partner_id`` only, when given: a company sees its own)."""
    row = load_agreement(db, item_id)
    if row is None or (partner_id is not None and row.partner_id != partner_id):
        raise HTTPException(status_code=404, detail="Agreement not found")
    return row


def invoice_out(row: Agreement, period: str, totals) -> InvoiceOut:
    today = clock.today()
    return InvoiceOut(
        agreement_id=row.id,
        period=period,
        payer=row.payer,
        status=totals.status(today),
        first_due_on=totals.first_due_on,
        last_due_on=totals.last_due_on,
        installments=totals.installments,
        employees=totals.employees,
        min_employees=totals.min_employees,
        below_minimum=totals.below_minimum,
        list_cents=totals.list_cents,
        discount_cents=totals.discount_cents,
        amount_cents=totals.amount_cents,
        company_share_cents=totals.company_share_cents,
        employee_share_cents=totals.employee_share_cents,
        company_fees_paid_cents=totals.company_fees_paid_cents,
        company_fees_open_cents=totals.company_fees_open_cents,
        company_paid_on=totals.company_paid_on,
        employee_paid_cents=totals.employee_paid_cents,
        employee_open_cents=totals.employee_open_cents,
        month_ended=totals.month_ended,
        shortfall_cents=totals.shortfall_cents,
        shortfall_due_on=totals.shortfall_due_on if totals.shortfall_cents else None,
        shortfall_paid_on=totals.shortfall_paid_on,
        shortfall_final=totals.month_ended or totals.shortfall_paid_on is not None,
        company_cents=totals.company_cents,
        company_open_cents=totals.company_open_cents,
    )


def invoices_of(row: Agreement, book: Ledger) -> list[InvoiceOut]:
    """One invoice per month with fees due: the employees' fees grouped by
    the month they are due, with the shortfall of the months below the minimum."""
    today = clock.today()
    return [invoice_out(row, period, _totals(row, book, period, today)) for period in book.periods()]


def invoice_detail(row: Agreement, book: Ledger, period: str) -> InvoiceDetailOut:
    if parse_period(period) is None:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    lines = book.lines(period)
    if not lines:
        raise HTTPException(status_code=404, detail=f"No fees due in {period}")
    today = clock.today()
    summary = invoice_out(row, period, _totals(row, book, period, today))
    return InvoiceDetailOut(
        **summary.model_dump(),
        lines=[
            InvoiceLineOut(
                subscription_id=subscription.id,
                customer_id=subscription.customer_id,
                customer_name=subscription.customer.name,
                modality_name=subscription.modality.name,
                installment_id=item.id,
                sequence=item.sequence,
                period_start=item.period_start,
                period_end=item.period_end,
                due_on=item.due_on,
                amount_cents=item.amount_cents,
                discount_cents=item.discount_cents or 0,
                company_share_cents=part_cents(item, PART_COMPANY),
                employee_share_cents=item.amount_cents - part_cents(item, PART_COMPANY),
                company_paid_on=item.company_paid_on,
                employee_paid_on=item.employee_paid_on,
                paid_on=item.paid_on,
                paid_cents=item.paid_cents,
                status=installment_status(item, today),
            )
            for subscription, item in lines
        ],
    )


# --- partners -----------------------------------------------------------------------


def enrolled_today(book: Ledger, today: date) -> int:
    """Employees whose enrollment covers today."""
    return len(
        {
            row.customer_id
            for row in book.enrollments
            if row.joined_on <= today <= row.expires_on and (row.cancelled_on is None or today <= row.cancelled_on)
        }
    )


def partner_out(row: Partner, books: dict[int, Ledger], today: date | None = None) -> PartnerOut:
    today = today or clock.today()
    in_force = [item for item in row.agreements if agreement_status(item, today) == AGREEMENT_IN_FORCE]
    return PartnerOut(
        id=row.id,
        name=row.name,
        cnpj=row.cnpj or "",
        contact_name=row.contact_name or "",
        email=row.email or "",
        phone=row.phone or "",
        city=row.city or "",
        state=row.state or "",
        notes=row.notes or "",
        active=bool(row.active),
        created_at=row.created_at,
        agreements_count=len(row.agreements),
        agreements_in_force=len(in_force),
        employees=sum(enrolled_today(books[item.id], today) for item in row.agreements),
    )


def _load_partner(db: Session, item_id: int) -> Partner:
    row = partner_query(db).filter(Partner.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    return row


def _unique_cnpj(db: Session, cnpj: str, exclude_id: int | None = None) -> None:
    digits = "".join(char for char in cnpj if char.isdigit())
    if not digits:
        return
    for row in db.query(Partner).filter(Partner.cnpj != "").all():
        if row.id != exclude_id and "".join(char for char in row.cnpj if char.isdigit()) == digits:
            raise HTTPException(status_code=409, detail=f"CNPJ already registered for partner {row.name}")


def _commit_partner(db: Session, item_id: int) -> PartnerOut:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Partner name already exists")
    db.expire_all()
    row = _load_partner(db, item_id)
    return partner_out(row, ledgers(db, [item.id for item in row.agreements]))


@partners.get("", response_model=list[PartnerOut])
def list_partners(db: Session = Depends(get_db)):
    rows = partner_query(db).order_by(Partner.name).all()
    books = ledgers(db)
    return [partner_out(row, books) for row in rows]


@partners.post("", response_model=PartnerOut, status_code=201, dependencies=WRITE)
def create_partner(body: PartnerIn, db: Session = Depends(get_db)):
    _unique_cnpj(db, body.cnpj)
    row = Partner(**body.model_dump())
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Partner name already exists")
    return _commit_partner(db, row.id)


@partners.get("/{item_id}", response_model=PartnerOut)
def get_partner(item_id: int, db: Session = Depends(get_db)):
    row = _load_partner(db, item_id)
    return partner_out(row, ledgers(db, [item.id for item in row.agreements]))


@partners.put("/{item_id}", response_model=PartnerOut, dependencies=WRITE)
def update_partner(item_id: int, body: PartnerIn, db: Session = Depends(get_db)):
    """Deactivating a partner suspends its agreements (no new enrollments;
    the employees enrolled keep their plans until they end) and revokes its
    portal logins at once."""
    row = _load_partner(db, item_id)
    _unique_cnpj(db, body.cnpj, exclude_id=item_id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    return _commit_partner(db, item_id)


@partners.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_partner(item_id: int, db: Session = Depends(get_db)):
    row = _load_partner(db, item_id)
    if row.agreements:
        raise HTTPException(
            status_code=409, detail="Partner has agreements: delete them first, or deactivate the partner to keep its history"
        )
    if db.query(User).filter(User.partner_id == item_id).count():
        raise HTTPException(status_code=409, detail="Partner has portal logins: delete them first, or deactivate the partner")
    db.delete(row)
    db.commit()


# --- agreements ---------------------------------------------------------------------


def _check_refs(db: Session, body) -> None:
    if db.get(Partner, body.partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    for item in body.plans:
        if db.get(Modality, item.modality_id) is None:
            raise HTTPException(status_code=404, detail=f"Plan {item.modality_id} not found")


def _apply_terms(row: Agreement, body, db: Session) -> None:
    """The contract's terms: six months from ``starts_on``."""
    row.partner_id = body.partner_id
    row.starts_on = body.starts_on
    row.ends_on = contract_end(body.starts_on)
    row.min_employees = body.min_employees
    row.payer = body.payer
    row.company_share_percent = {"company": 100, "employee": 0}.get(body.payer, body.company_share_percent)
    # Replace the plans: delete first so the unique keys are free.
    row.plans.clear()
    db.flush()
    row.plans.extend(
        AgreementPlan(
            modality_id=item.modality_id,
            discount_percent=item.discount_percent,
            grace_days=item.grace_days,
            company_share_percent=item.company_share_percent,
        )
        for item in body.plans
    )


def _frozen_check(row: Agreement, body: AgreementIn) -> None:
    wanted = (
        body.partner_id,
        body.starts_on,
        int(body.min_employees),
        body.payer,
        {"company": 100, "employee": 0}.get(body.payer, body.company_share_percent),
        tuple(
            sorted((item.modality_id, int(item.discount_percent), item.grace_days, item.company_share_percent) for item in body.plans)
        ),
    )
    if wanted != terms_of(row):
        raise HTTPException(
            status_code=409,
            detail="The terms are frozen once employees are enrolled: only the name, the notes and the activity may change. "
            "Renegotiate with a new contract (renew it).",
        )


@agreements.get("", response_model=list[AgreementOut])
def list_agreements(partner_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)):
    """``status``: scheduled | in_force | ended | suspended."""
    if status is not None and status not in AGREEMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(AGREEMENT_STATUSES)}")
    query = agreement_query(db)
    if partner_id is not None:
        query = query.filter(Agreement.partner_id == partner_id)
    rows = query.order_by(Agreement.starts_on.desc(), Agreement.id.desc()).all()
    books = ledgers(db, [row.id for row in rows])
    result = [agreement_out(row, books[row.id]) for row in rows]
    return [item for item in result if status is None or item.status == status]


@agreements.post("", response_model=AgreementOut, status_code=201, dependencies=WRITE)
def create_agreement(body: AgreementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A six-month contract: ``ends_on`` follows from ``starts_on``."""
    _check_refs(db, body)
    row = Agreement(created_by_id=user.id, name=body.name, notes=body.notes, active=body.active)
    db.add(row)
    _apply_terms(row, body, db)
    db.commit()
    return agreement_out(load_or_404(db, row.id), ledger(db, row.id))


@agreements.get("/{item_id}", response_model=AgreementOut)
def get_agreement(item_id: int, db: Session = Depends(get_db)):
    return agreement_out(load_or_404(db, item_id), ledger(db, item_id))


@agreements.put("/{item_id}", response_model=AgreementOut, dependencies=WRITE)
def update_agreement(item_id: int, body: AgreementIn, db: Session = Depends(get_db)):
    """Before any enrollment the whole contract may change; afterwards its
    terms are frozen (the employees' conditions were snapshotted on their
    enrollments): only the name, the notes and the activity change."""
    row = load_or_404(db, item_id)
    _check_refs(db, body)
    if has_enrollments(db, item_id):
        _frozen_check(row, body)
    else:
        _apply_terms(row, body, db)
    row.name, row.notes, row.active = body.name, body.notes, body.active
    db.commit()
    db.expire_all()
    return agreement_out(load_or_404(db, item_id), ledger(db, item_id))


@agreements.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_agreement(item_id: int, db: Session = Depends(get_db)):
    row = load_or_404(db, item_id)
    if has_enrollments(db, item_id):
        raise HTTPException(
            status_code=409, detail="The agreement has enrollments: suspend it instead (they keep their history)"
        )
    db.delete(row)
    db.commit()


@agreements.post("/{item_id}/renew", response_model=AgreementOut, status_code=201, dependencies=WRITE)
def renew_agreement(
    item_id: int, body: AgreementRenewIn | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The next six-month contract, starting the day after this one ends,
    with the same terms unless new ones are given (a renegotiation). With
    ``carry_employees`` (default) every employee enrolled until the end of
    this contract is enrolled in the new one, in the same plan when the new
    contract still covers it — under its new, frozen terms."""
    from .subscriptions import record_subscription  # the subscriptions router imports this module

    body = body or AgreementRenewIn()
    current = load_or_404(db, item_id)
    payer = body.payer or current.payer
    shared = payer == "shared"
    try:
        terms = AgreementIn(
            partner_id=current.partner_id,
            name=body.name or f"{current.name} (renewed)",
            starts_on=current.ends_on + timedelta(days=1),
            min_employees=body.min_employees or current.min_employees,
            payer=payer,
            company_share_percent=body.company_share_percent
            if body.company_share_percent is not None
            else (current.company_share_percent if shared and current.payer == "shared" else None),
            active=True,
            notes=body.notes if body.notes is not None else current.notes,
            plans=body.plans
            if body.plans is not None
            else [
                {
                    "modality_id": item.modality_id,
                    "discount_percent": item.discount_percent,
                    "grace_days": item.grace_days,
                    # A plan's own split only carries into another co-payment.
                    "company_share_percent": item.company_share_percent if shared else None,
                }
                for item in current.plans
            ],
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=[{"msg": item["msg"]} for item in error.errors()])
    _check_refs(db, terms)
    following = Agreement(created_by_id=user.id, name=terms.name, notes=terms.notes, active=True)
    db.add(following)
    _apply_terms(following, terms, db)
    db.flush()
    if body.carry_employees:
        covered = {item.modality_id for item in terms.plans}
        staying = (
            db.query(Subscription)
            .filter(
                Subscription.agreement_id == current.id,
                Subscription.cancelled_on.is_(None),
                Subscription.expires_on == current.ends_on,
                Subscription.modality_id.in_(sorted(covered)),
            )
            .order_by(Subscription.id)
            .all()
        )
        from ..schemas import SubscriptionIn

        for row in staying:
            record_subscription(
                db,
                SubscriptionIn(
                    customer_id=row.customer_id,
                    modality_id=row.modality_id,
                    joined_on=following.starts_on,
                    expires_on=following.ends_on,
                    agreement_id=following.id,
                    # A co-payment keeps the split the company and the employee had agreed.
                    company_share_percent=share_on(row.shares, row.expires_on) if terms.payer == "shared" else None,
                ),
                user,
                None,
                following.id,
            )
    db.commit()
    return agreement_out(load_or_404(db, following.id), ledger(db, following.id))


# --- the company's invoices ------------------------------------------------------------


@agreements.get("/{item_id}/invoices", response_model=list[InvoiceOut])
def list_invoices(item_id: int, db: Session = Depends(get_db)):
    return invoices_of(load_or_404(db, item_id), ledger(db, item_id))


@agreements.get("/{item_id}/invoices/{period}", response_model=InvoiceDetailOut)
def get_invoice(item_id: int, period: str, db: Session = Depends(get_db)):
    return invoice_detail(load_or_404(db, item_id), ledger(db, item_id), period)


@agreements.post("/{item_id}/invoices/{period}/pay", response_model=InvoiceDetailOut)
def pay_invoice(
    item_id: int, period: str, body: InvoicePay, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The company paid its invoice of the month: every open fee due in it
    (when the company pays them) is registered as paid on ``paid_on`` at its
    amount — taxes, the employees' notifications and plan status follow, as
    for a single payment — and, once the month ended, the shortfall of a
    month below the minimum is recorded (amount and headcount snapshotted,
    with its taxes). The invoice of a month is paid from its first day."""
    row = load_or_404(db, item_id)
    bounds = parse_period(period)
    if bounds is None:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    book = ledger(db, item_id)
    lines = book.lines(period)
    if not lines:
        raise HTTPException(status_code=404, detail=f"No fees due in {period}")
    today = clock.today()
    if today < bounds[0]:
        raise HTTPException(status_code=409, detail=f"The invoice of {period} is paid from the first day of the month")
    totals = _totals(row, book, period, today)
    # The company's part of every fee still open (all of each fee when it pays them; its share in a co-payment).
    fees = [
        (subscription, item)
        for subscription, item in lines
        if part_cents(item, PART_COMPANY) and item.company_paid_on is None
    ]
    charge_due = totals.shortfall_payable
    if not fees and not charge_due:
        if totals.shortfall_open:
            detail = f"The shortfall of {period} is charged once the month ends (the headcount may still change)"
        elif not totals.company_share_cents and not totals.shortfall_cents:
            detail = f"Nothing for the company to pay in {period}: the employees pay their own fees and the minimum was met"
        else:
            detail = f"The invoice of {period} is already paid"
        raise HTTPException(status_code=409, detail=detail)
    settled = []
    for subscription, item in fees:
        register_part(item, PART_COMPANY, body.paid_on, None, user.id)
        if item.paid_on is not None:  # the employee's part was in (or there is none): the fee is paid
            apply_installment_taxes(db, subscription, item)
            settled.append((subscription, item))
    shortfall = 0
    if charge_due:
        shortfall = totals.shortfall_cents
        charge = AgreementCharge(
            agreement_id=row.id,
            period=period,
            employees=totals.employees,
            min_employees=totals.min_employees,
            amount_cents=shortfall,
            paid_on=body.paid_on,
            registered_by_id=user.id,
        )
        db.add(charge)
        apply_charge_taxes(db, charge)
    db.flush()
    for subscription, item in fees:
        if (subscription, item) in settled:
            db.expire(subscription, ["installments"])
            installment_paid(db, subscription, item, subscription_status(subscription, subscription.installments, today))
        else:
            installment_part_paid(db, subscription, item, PART_COMPANY)
    agreement_invoice_paid(
        db, row, period, len(fees), sum(item.company_paid_cents for _, item in fees), shortfall, body.paid_on, user
    )
    db.commit()
    db.expire_all()
    return invoice_detail(load_or_404(db, item_id), ledger(db, item_id), period)


@agreements.delete("/{item_id}/invoices/{period}/shortfall", response_model=InvoiceDetailOut)
def undo_shortfall(item_id: int, period: str, db: Session = Depends(get_db)):
    """Undo the shortfall payment registered for the month (a mistake): it
    is projected again. The fees' payments are undone on each subscription."""
    row = load_or_404(db, item_id)
    charge = _charges(row).get(period)
    if charge is None:
        raise HTTPException(status_code=404, detail=f"No shortfall payment registered for {period}")
    db.delete(charge)
    db.commit()
    db.expire_all()
    return invoice_detail(load_or_404(db, item_id), ledger(db, item_id), period)


# --- co-payment: the split, and the company's statement ------------------------------------


def change_agreement_share(db: Session, row: Agreement, body: ShareChangeIn, actor: User, company: bool) -> ShareChangeOut:
    """A new split for every employee of the agreement still enrolled on
    ``effective_from`` (their open fees from then on are split again)."""
    effective_from = share_effective_from(body.effective_from, company)
    enrolled = (
        db.query(Subscription)
        .filter(
            Subscription.agreement_id == row.id,
            Subscription.cancelled_on.is_(None),
            Subscription.expires_on >= effective_from,
        )
        .order_by(Subscription.id)
        .all()
    )
    for subscription in enrolled:
        record_share(db, subscription, body.company_share_percent, effective_from, actor, body.note)
    return ShareChangeOut(changed=len(enrolled), effective_from=effective_from, company_share_percent=body.company_share_percent)


@agreements.put("/{item_id}/share", response_model=ShareChangeOut)
def change_share(item_id: int, body: ShareChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Co-payment: the company and its employees agreed a new split — for
    every employee enrolled (any date, on the company's word)."""
    row = load_or_404(db, item_id)
    result = change_agreement_share(db, row, body, user, company=False)
    db.commit()
    return result


def statement_range(start: str | None, end: str | None) -> tuple[str, str]:
    default_start, default_end = default_range()
    return start or default_start, end or default_end


@partners.get("/{item_id}/statement", response_model=CompanyStatementOut)
def partner_statement(
    item_id: int,
    start: str | None = Query(default=None, description="YYYY-MM; default January of this year"),
    end: str | None = Query(default=None, description="YYYY-MM; default December of this year"),
    agreement_id: int | None = None,
    db: Session = Depends(get_db),
):
    """What the company pays and what its employees pay, by month and by employee."""
    start, end = statement_range(start, end)
    return company_statement(db, _load_partner(db, item_id), start, end, agreement_id)


@partners.get("/{item_id}/statement.csv")
def partner_statement_csv(
    item_id: int, start: str | None = None, end: str | None = None, agreement_id: int | None = None, db: Session = Depends(get_db)
):
    start, end = statement_range(start, end)
    partner = _load_partner(db, item_id)
    return csv_response(company_statement_csv(db, partner, start, end, agreement_id), f"statement_{partner.id}_{start}_{end}.csv")


def csv_response(text: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([text]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

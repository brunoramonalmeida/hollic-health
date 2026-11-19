"""The partner companies' self-service portal (role ``partner``).

A company's login sees its own company only: its agreements (terms, the
minimum of employees, the headcount of each month, the shortfall) and its
monthly invoices, and it **manages its employees**: enrolls them in an
agreement (under the same rules as the staff — the plan covered, the six
months of the contract, frozen conditions) and removes them when they leave.
It never sees the employees' health records, appointments or anything else
of the network; payments are registered by Hollic Health's staff.
"""

import unicodedata
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..auth import get_current_user, require_partner
from ..billing import (
    current_subscription,
    dependents_count,
    list_amount,
    locked,
    monthly_amount,
    subscription_status,
    sync_installments,
    waiting_until,
)
from ..copayment import company_statement, company_statement_csv, current_share, history_out, monthly_split, record_share, share_effective_from
from ..database import get_db
from ..models import Agreement, Appointment, Customer, Dependent, EnrollmentShare, Subscription, User
from ..notifications import (
    employee_changed_by_company,
    employee_enrolled_by_company,
    employee_removed_by_company,
    subscription_cancelled,
)
from ..categories import plan_grace_days
from ..partners import grace_days_for, ledger, ledgers, offer_for, parse_period
from ..schemas import (
    AgreementOut,
    CompanyStatementOut,
    InvoiceDetailOut,
    InvoiceOut,
    PortalCancelOut,
    PortalDependentIn,
    PortalDependentOut,
    PortalEmployeeCancel,
    PortalEmployeeDetailOut,
    PortalEmployeeIn,
    PortalEmployeeOut,
    PortalEmployeeUpdate,
    PortalOut,
    ShareChangeIn,
    ShareChangeOut,
    SubscriptionIn,
)
from .partners import (
    _load_partner,
    agreement_out,
    change_agreement_share,
    csv_response,
    enrolled_today,
    invoice_detail,
    invoices_of,
    load_or_404,
    partner_out,
    statement_range,
    track_minimum,
)
from .subscriptions import _apply_cancel, _apply_update, record_subscription

router = APIRouter(prefix="/portal", tags=["portal"], dependencies=[Depends(require_partner)])


def _agreements(db: Session, partner_id: int) -> list[Agreement]:
    from ..partners import agreement_query

    return (
        agreement_query(db)
        .filter(Agreement.partner_id == partner_id)
        .order_by(Agreement.starts_on.desc(), Agreement.id.desc())
        .all()
    )


def _digits(value: str | None) -> str:
    return "".join(char for char in (value or "") if char.isdigit())


def _same_name(left: str, right: str) -> bool:
    def plain(value: str) -> str:
        text = unicodedata.normalize("NFKD", value or "")
        return " ".join("".join(char for char in text if not unicodedata.combining(char)).casefold().split())

    return plain(left) == plain(right)


def employee_out(db: Session, row: Subscription, today: date | None = None) -> PortalEmployeeOut:
    """What the company sees of an enrollment: no health data."""
    today = today or clock.today()
    dependents = dependents_count(db, row.customer_id)
    offer = offer_for(row.agreement.plans, row.modality_id)
    items = row.installments
    company_monthly, employee_monthly = monthly_split(db, row, today)
    grace = grace_days_for(offer, plan_grace_days(row.modality))
    return PortalEmployeeOut(
        subscription_id=row.id,
        agreement_id=row.agreement_id,
        agreement_name=row.agreement.name,
        customer_id=row.customer_id,
        name=row.customer.name,
        document=row.customer.document or "",
        email=row.customer.email or "",
        phone=row.customer.phone or "",
        modality_id=row.modality_id,
        modality_name=row.modality.name,
        joined_on=row.joined_on,
        expires_on=row.expires_on,
        cancelled_on=row.cancelled_on,
        status=subscription_status(row, items, today),
        usable_from=waiting_until(row.joined_on, grace),
        grace_days=grace,
        birth_date=row.customer.birth_date,
        dependents_count=dependents,
        monthly_amount_cents=monthly_amount(row.modality, dependents, row.discount_percent or 0),
        list_amount_cents=list_amount(row.modality, dependents),
        discount_percent=row.discount_percent or 0,
        installments_paid=sum(1 for item in items if item.paid_on is not None),
        installments_total=len(items),
        payer=row.agreement.payer,
        company_share_percent=current_share(row, today),
        company_monthly_cents=company_monthly,
        employee_monthly_cents=employee_monthly,
        share_history=history_out(row),
    )


def _employees_query(db: Session, partner_id: int):
    return (
        db.query(Subscription)
        .join(Agreement, Subscription.agreement_id == Agreement.id)
        .options(
            joinedload(Subscription.customer),
            joinedload(Subscription.modality),
            joinedload(Subscription.agreement).selectinload(Agreement.plans),
            selectinload(Subscription.installments),
            selectinload(Subscription.shares).joinedload(EnrollmentShare.created_by),
        )
        .filter(Agreement.partner_id == partner_id)
    )


def _load_employee(db: Session, user: User, subscription_id: int) -> Subscription:
    row = _employees_query(db, user.partner_id).filter(Subscription.id == subscription_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Employee enrollment not found")
    return row


# --- the company and its agreements ------------------------------------------------------


@router.get("", response_model=PortalOut)
def portal_home(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The company's home: its record, its agreements with the headcount of
    this month against the minimum, and what it still owes."""
    today = clock.today()
    partner = _load_partner(db, user.partner_id)
    rows = _agreements(db, user.partner_id)
    books = ledgers(db, [row.id for row in rows])
    agreements = [agreement_out(row, books[row.id], today) for row in rows]
    owed = sum(
        invoice.company_open_cents
        for row in rows
        for invoice in invoices_of(row, books[row.id])
        if parse_period(invoice.period)[0] <= today
    )
    return PortalOut(
        partner=partner_out(partner, books, today),
        agreements=agreements,
        employees_enrolled=sum(enrolled_today(books[row.id], today) for row in rows),
        shortfall_open_cents=sum(item.shortfall_open_cents for item in agreements),
        company_open_cents=owed,
    )


@router.get("/agreements", response_model=list[AgreementOut])
def portal_agreements(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = _agreements(db, user.partner_id)
    books = ledgers(db, [row.id for row in rows])
    return [agreement_out(row, books[row.id]) for row in rows]


@router.get("/agreements/{item_id}", response_model=AgreementOut)
def portal_agreement(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return agreement_out(load_or_404(db, item_id, user.partner_id), ledger(db, item_id))


@router.get("/agreements/{item_id}/invoices", response_model=list[InvoiceOut])
def portal_invoices(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return invoices_of(load_or_404(db, item_id, user.partner_id), ledger(db, item_id))


@router.get("/agreements/{item_id}/invoices/{period}", response_model=InvoiceDetailOut)
def portal_invoice(item_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return invoice_detail(load_or_404(db, item_id, user.partner_id), ledger(db, item_id), period)


# --- the employees ---------------------------------------------------------------------------


@router.get("/employees", response_model=list[PortalEmployeeOut])
def portal_employees(
    agreement_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Every enrollment of the company's agreements (``agreement_id``: one of them)."""
    query = _employees_query(db, user.partner_id)
    if agreement_id is not None:
        query = query.filter(Subscription.agreement_id == agreement_id)
    rows = query.all()
    today = clock.today()
    rows.sort(key=lambda row: (row.cancelled_on is not None, row.customer.name.lower(), -row.id))
    return [employee_out(db, row, today) for row in rows]


def _person(db: Session, body: PortalEmployeeIn, partner) -> Customer:
    """The employee's record: found by CPF (the name must match the one on
    file, so a CPF alone reveals nobody) or registered with what the company
    gave — nothing else of an existing record is changed."""
    digits = _digits(body.document)
    for row in db.query(Customer).filter(Customer.document != "").all():
        if _digits(row.document) != digits:
            continue
        if not _same_name(row.name, body.name):
            raise HTTPException(
                status_code=409,
                detail="This CPF is registered under another name: check the employee's data or contact Hollic Health",
            )
        return row
    row = Customer(
        name=body.name,
        document=body.document,
        birth_date=body.birth_date,
        email=body.email,
        phone=body.phone,
        city=partner.city or "",
        state=partner.state or "",
    )
    db.add(row)
    db.flush()
    return row


@router.post("/employees", response_model=PortalEmployeeOut, status_code=201)
def portal_enroll(body: PortalEmployeeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The company enrolls an employee in one of its agreements, in a plan
    the agreement covers, from ``joined_on`` (today or later; default today,
    or the contract's start) until the end of the contract, under its frozen
    conditions. The employee is told; so is the staff."""
    agreement = load_or_404(db, body.agreement_id, user.partner_id)
    today = clock.today()
    joined_on = body.joined_on or max(today, agreement.starts_on)
    if joined_on < today:
        raise HTTPException(status_code=409, detail="An enrollment starts today or later")
    if joined_on >= agreement.ends_on:
        raise HTTPException(
            status_code=409, detail=f"Agreement {agreement.name} ends on {agreement.ends_on:%Y-%m-%d}: enroll in the next contract"
        )
    person = _person(db, body, agreement.partner)
    overlapping = [
        row
        for row in db.query(Subscription)
        .options(joinedload(Subscription.agreement))
        .filter(Subscription.customer_id == person.id, Subscription.cancelled_on.is_(None))
        .all()
        if row.joined_on <= agreement.ends_on and joined_on <= row.expires_on
    ]
    if overlapping:
        other = overlapping[0]
        if other.agreement is not None and other.agreement.partner_id == user.partner_id:
            detail = f"{person.name} is already enrolled in {other.agreement.name} until {other.expires_on:%Y-%m-%d}"
        else:
            detail = f"{person.name} already has a plan with Hollic Health for that period: contact Hollic Health to move it into your agreement"
        raise HTTPException(status_code=409, detail=detail)
    row = record_subscription(
        db,
        SubscriptionIn(
            customer_id=person.id,
            modality_id=body.modality_id,
            joined_on=joined_on,
            expires_on=agreement.ends_on,
            agreement_id=agreement.id,
            company_share_percent=body.company_share_percent,  # the split agreed with the employee
        ),
        user,
        None,
        agreement.id,
    )
    db.flush()
    row = _load_employee(db, user, row.id)
    employee_enrolled_by_company(db, row, user)
    db.commit()
    return employee_out(db, _load_employee(db, user, row.id))


@router.post("/employees/{subscription_id}/cancel", response_model=PortalCancelOut)
def portal_remove(
    subscription_id: int,
    body: PortalEmployeeCancel | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The employee left the company: the enrollment is cancelled on
    ``cancelled_on`` (today or later; the fees of the periods starting after
    it are no longer owed). An enrollment that has not started and has
    nothing paid is withdrawn altogether."""
    row = _load_employee(db, user, subscription_id)
    if row.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="The enrollment is already cancelled")
    today = clock.today()
    cancelled_on = (body.cancelled_on if body else None) or today
    if cancelled_on < today:
        raise HTTPException(status_code=409, detail="The cancellation date cannot be in the past")
    agreement_id = row.agreement_id
    if today < row.joined_on and not any(locked(item) for item in row.installments):
        row.cancelled_on = today  # for the notices: the record goes away with its fees
        subscription_cancelled(db, row, user)
        employee_removed_by_company(db, row, user, withdrawn=True)
        db.delete(row)
        db.flush()
        track_minimum(db, [agreement_id], user)
        db.commit()
        return PortalCancelOut(withdrawn=True, employee=None)
    _apply_cancel(db, row, max(cancelled_on, row.joined_on), user)
    db.flush()
    employee_removed_by_company(db, row, user)
    track_minimum(db, [agreement_id], user)
    db.commit()
    return PortalCancelOut(withdrawn=False, employee=employee_out(db, _load_employee(db, user, subscription_id)))


# --- co-payment and the statement -------------------------------------------------------------


@router.put("/employees/{subscription_id}/share", response_model=PortalEmployeeOut)
def portal_employee_share(
    subscription_id: int, body: ShareChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Co-payment: the company and the employee agreed a new split — from
    ``effective_from`` (today or later; default today) on, the company pays
    ``company_share_percent`` of the employee's fees. The employee is told."""
    row = _load_employee(db, user, subscription_id)
    record_share(db, row, body.company_share_percent, share_effective_from(body.effective_from, True), user, body.note)
    db.commit()
    db.expire_all()
    return employee_out(db, _load_employee(db, user, subscription_id))


@router.put("/agreements/{item_id}/share", response_model=ShareChangeOut)
def portal_agreement_share(item_id: int, body: ShareChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Co-payment: a new split for every employee of the agreement, from
    ``effective_from`` (today or later) on."""
    row = load_or_404(db, item_id, user.partner_id)
    result = change_agreement_share(db, row, body, user, company=True)
    db.commit()
    return result


@router.get("/statement", response_model=CompanyStatementOut)
def portal_statement(
    start: str | None = Query(default=None, description="YYYY-MM; default January of this year"),
    end: str | None = Query(default=None, description="YYYY-MM; default December of this year"),
    agreement_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What the company pays and what its employees pay, month by month and
    employee by employee (by the month the fees are due)."""
    if agreement_id is not None:
        load_or_404(db, agreement_id, user.partner_id)
    start, end = statement_range(start, end)
    return company_statement(db, _load_partner(db, user.partner_id), start, end, agreement_id)


@router.get("/statement.csv")
def portal_statement_csv(
    start: str | None = None,
    end: str | None = None,
    agreement_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The payroll list: one row per employee fee — the company's part, the employee's part and their payments."""
    if agreement_id is not None:
        load_or_404(db, agreement_id, user.partner_id)
    start, end = statement_range(start, end)
    partner = _load_partner(db, user.partner_id)
    return csv_response(company_statement_csv(db, partner, start, end, agreement_id), f"statement_{start}_{end}.csv")


# --- editing an employee and its dependents -------------------------------------------------


def _editable(row: Subscription) -> None:
    """The company manages an employee while it has it enrolled (not
    cancelled, not ended)."""
    if row.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="The enrollment is cancelled: the employee is no longer the company's to manage")
    if row.expires_on < clock.today():
        raise HTTPException(status_code=409, detail=f"The enrollment ended on {row.expires_on:%Y-%m-%d}")


def dependent_out(row: Dependent) -> PortalDependentOut:
    """A dependent as the company sees it: no health data."""
    return PortalDependentOut(
        id=row.id,
        name=row.name,
        birth_date=row.birth_date,
        kinship=row.kinship or "",
        document=row.document or "",
        sex=row.sex or "",
    )


def employee_detail(db: Session, row: Subscription) -> PortalEmployeeDetailOut:
    dependents = db.query(Dependent).filter(Dependent.customer_id == row.customer_id).order_by(Dependent.name).all()
    return PortalEmployeeDetailOut(
        **employee_out(db, row).model_dump(),
        dependents=[dependent_out(item) for item in dependents],
        dependent_limit=row.modality.dependent_limit,
        extra_dependent_cents=row.modality.extra_dependent_cents,
    )


def _reprice(db: Session, row: Subscription) -> None:
    """Dependents changed: the open fees follow (extra dependents above the
    plan's limit), of this enrollment and of the customer's plan in force."""
    db.flush()
    sync_installments(db, row)
    current = current_subscription(db, row.customer_id)
    if current is not None and current.id != row.id:
        sync_installments(db, current)


def _load_dependent(db: Session, row: Subscription, dependent_id: int) -> Dependent:
    dependent = db.get(Dependent, dependent_id)
    if dependent is None or dependent.customer_id != row.customer_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    return dependent


@router.get("/employees/{subscription_id}", response_model=PortalEmployeeDetailOut)
def portal_employee(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One employee of the company: its enrollment, its split and its dependents."""
    return employee_detail(db, _load_employee(db, user, subscription_id))


@router.put("/employees/{subscription_id}", response_model=PortalEmployeeDetailOut)
def portal_edit_employee(
    subscription_id: int, body: PortalEmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The company corrects its employee's identification and contact, moves
    it to another plan of the agreement (its open fees follow the plan's
    negotiated conditions) or, before the enrollment starts, changes its
    start (today or later). The employee is told."""
    row = _load_employee(db, user, subscription_id)
    _editable(row)
    today = clock.today()
    person = row.customer
    changes = []
    digits = _digits(body.document)
    if digits != _digits(person.document):
        clash = next(
            (other for other in db.query(Customer).filter(Customer.document != "", Customer.id != person.id).all() if _digits(other.document) == digits),
            None,
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail="This CPF belongs to another person registered with Hollic Health")
    for field, label in (("name", "name"), ("document", "CPF"), ("birth_date", "birth date"), ("email", "email"), ("phone", "phone")):
        value = getattr(body, field)
        if value != getattr(person, field) and not (value in (None, "") and getattr(person, field) in (None, "")):
            setattr(person, field, value if value is not None else (None if field == "birth_date" else ""))
            changes.append(label)
    modality_id = body.modality_id or row.modality_id
    joined_on = body.joined_on or row.joined_on
    if joined_on != row.joined_on:
        if today >= row.joined_on or any(locked(item) for item in row.installments):
            raise HTTPException(status_code=409, detail="The enrollment has started: its start cannot change")
        if joined_on < today:
            raise HTTPException(status_code=409, detail="An enrollment starts today or later")
        if joined_on >= row.expires_on:
            raise HTTPException(status_code=409, detail=f"The enrollment ends on {row.expires_on:%Y-%m-%d}")
        changes.append(f"start {row.joined_on:%d/%m/%Y} → {joined_on:%d/%m/%Y}")
    if (modality_id, joined_on) != (row.modality_id, row.joined_on):
        previous_plan = row.modality.name
        # The same rules as the staff's edit: a plan the agreement covers, inside its six months.
        touched = _apply_update(
            db,
            row,
            SubscriptionIn(
                customer_id=row.customer_id,
                modality_id=modality_id,
                joined_on=joined_on,
                expires_on=row.expires_on,
                agreement_id=row.agreement_id,
            ),
            None,
            row.agreement_id,
        )
        track_minimum(db, touched, user)
        if row.modality.name != previous_plan:
            changes.append(f"plan {previous_plan} → {row.modality.name}")
    db.flush()
    if changes:
        employee_changed_by_company(db, row, "Changed: " + ", ".join(changes) + ".", user)
    db.commit()
    db.expire_all()
    return employee_detail(db, _load_employee(db, user, subscription_id))


@router.get("/employees/{subscription_id}/dependents", response_model=list[PortalDependentOut])
def portal_dependents(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return employee_detail(db, _load_employee(db, user, subscription_id)).dependents


@router.post("/employees/{subscription_id}/dependents", response_model=PortalEmployeeDetailOut, status_code=201)
def portal_add_dependent(
    subscription_id: int, body: PortalDependentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The company adds a dependent to its employee's plan: the fees follow
    (each dependent above the plan's limit costs the extra price). The
    employee is told."""
    row = _load_employee(db, user, subscription_id)
    _editable(row)
    db.add(Dependent(customer_id=row.customer_id, **body.model_dump()))
    _reprice(db, row)
    employee_changed_by_company(db, row, f"Dependent added: {body.name} ({body.kinship or 'dependent'}).", user)
    db.commit()
    db.expire_all()
    return employee_detail(db, _load_employee(db, user, subscription_id))


@router.put("/employees/{subscription_id}/dependents/{dependent_id}", response_model=PortalEmployeeDetailOut)
def portal_edit_dependent(
    subscription_id: int,
    dependent_id: int,
    body: PortalDependentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The company corrects a dependent's identification (its health data stays untouched)."""
    row = _load_employee(db, user, subscription_id)
    _editable(row)
    dependent = _load_dependent(db, row, dependent_id)
    for key, value in body.model_dump().items():
        setattr(dependent, key, value)
    db.flush()
    employee_changed_by_company(db, row, f"Dependent updated: {body.name}.", user)
    db.commit()
    db.expire_all()
    return employee_detail(db, _load_employee(db, user, subscription_id))


@router.delete("/employees/{subscription_id}/dependents/{dependent_id}", response_model=PortalEmployeeDetailOut)
def portal_remove_dependent(
    subscription_id: int, dependent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The company takes a dependent off its employee's plan: the fees follow.
    A dependent with appointments stays (its history): Hollic Health removes it."""
    row = _load_employee(db, user, subscription_id)
    _editable(row)
    dependent = _load_dependent(db, row, dependent_id)
    name = dependent.name
    if db.query(Appointment.id).filter(Appointment.dependent_id == dependent.id).first() is not None:
        raise HTTPException(status_code=409, detail=f"{name} has appointments: contact Hollic Health to remove the dependent")
    try:
        db.delete(dependent)
        _reprice(db, row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{name} has appointments: contact Hollic Health to remove the dependent")
    employee_changed_by_company(db, row, f"Dependent removed: {name}.", user)
    db.commit()
    db.expire_all()
    return employee_detail(db, _load_employee(db, user, subscription_id))

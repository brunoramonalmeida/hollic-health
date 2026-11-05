"""Co-payment of corporate plans: the split of each employee's fees between
the company and the employee, its changes over time, and the statements of
what each side pays.

In a co-payment agreement (``payer: shared``) every fee of an enrollment is
split: the company pays ``company_share_percent`` of it (rounded half up)
and the employee the rest. The split of each employee is **agreed between
the company and the employee** and may change over time: each change is an
``EnrollmentShare`` row effective from a date; the row in force on the first
day of a fee's period splits that fee, unless a part of it was already paid
(then it keeps its split — app.billing). The company records the changes in
its portal (from today on), the staff on its word (any date). Agreements
where the company pays everything (100 %) or the employees do (0 %) have a
fixed split.

The **statements** put the two sides next to each other, by the month the
fees are due: the company's (its part of every employee's fees, the
shortfall of the months below the minimum, what it paid and owes, and the
employees one by one — the list a payroll deduction needs) and the
employee's (its own part of every fee, what its company paid, what is open).
"""

import csv
import io
from collections import defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from . import clock
from .billing import (
    INSTALLMENT_OVERDUE,
    PART_COMPANY,
    PART_EMPLOYEE,
    installment_status,
    is_split,
    monthly_amount,
    part_cents,
    sync_installments,
    dependents_count,
)
from .models import Agreement, Customer, EnrollmentShare, Subscription, User
from .notifications import share_changed
from .partners import (
    PAYER_SHARED,
    contract_share,
    default_share,
    invoice_period,
    invoice_totals,
    ledgers,
    offer_for,
    parse_period,
    share_on,
    split_cents,
)
from .schemas import (
    CompanyStatementOut,
    EmployeeStatementLine,
    EmployeeStatementOut,
    EmployeeStatementTotals,
    EnrollmentShareOut,
    StatementEmployee,
    StatementMonth,
)

SHARE_FIXED = "The split is fixed in this agreement: {who}. Co-payment splits change only in a co-payment agreement"


# --- the split ---------------------------------------------------------------------------


def current_share(subscription: Subscription, today: date | None = None) -> int | None:
    """The company's part of the enrollment's fees in force today (clamped
    into the enrollment); None for an individual plan."""
    if not is_split(subscription):
        return None
    today = today or clock.today()
    day = min(max(today, subscription.joined_on), subscription.expires_on)
    percent = share_on(subscription.shares, day)
    if percent is None and subscription.agreement is not None:
        percent = default_share(subscription.agreement, offer_for(subscription.agreement.plans, subscription.modality_id))
    return percent


def history_out(subscription: Subscription) -> list[EnrollmentShareOut]:
    return [
        EnrollmentShareOut(
            company_share_percent=row.company_share_percent,
            employee_share_percent=100 - row.company_share_percent,
            effective_from=row.effective_from,
            note=row.note or "",
            created_by_name=row.created_by.name if row.created_by is not None else None,
            created_at=row.created_at,
        )
        for row in sorted(subscription.shares, key=lambda item: item.effective_from)
    ]


def monthly_split(db: Session, subscription: Subscription, today: date | None = None) -> tuple[int, int]:
    """(company, employee) monthly amounts at the split in force today."""
    amount = monthly_amount(subscription.modality, dependents_count(db, subscription.customer_id), subscription.discount_percent or 0)
    if not is_split(subscription):
        return 0, amount
    company = split_cents(amount, current_share(subscription, today) or 0)
    return company, amount - company


def initial_share(agreement: Agreement, modality_id: int, requested: int | None) -> int:
    """The split of a new enrollment: the one the company and the employee
    agreed (co-payment only), else the plan's or the agreement's default."""
    if requested is not None and agreement.payer != PAYER_SHARED and requested != contract_share(agreement):
        raise HTTPException(status_code=409, detail=SHARE_FIXED.format(who=_fixed_label(agreement)))
    if requested is not None:
        return requested
    return default_share(agreement, offer_for(agreement.plans, modality_id))


def _fixed_label(agreement: Agreement) -> str:
    return "the company pays every fee" if contract_share(agreement) == 100 else "each employee pays its fees"


def record_share(
    db: Session,
    subscription: Subscription,
    percent: int,
    effective_from: date,
    actor: User | None,
    note: str = "",
    notify: bool = True,
) -> None:
    """From ``effective_from`` on, the company pays ``percent`` of the
    employee's fees: the open fees of the periods starting then are split
    again (those with a part paid keep theirs) and the employee is told."""
    agreement = subscription.agreement
    if agreement is None:
        raise HTTPException(status_code=409, detail="Only a corporate enrollment has a split")
    if agreement.payer != PAYER_SHARED:
        raise HTTPException(status_code=409, detail=SHARE_FIXED.format(who=_fixed_label(agreement)))
    if subscription.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="The enrollment is cancelled")
    if effective_from > subscription.expires_on:
        raise HTTPException(status_code=409, detail=f"The enrollment ends on {subscription.expires_on:%Y-%m-%d}")
    effective_from = max(effective_from, subscription.joined_on)
    previous = share_on(subscription.shares, effective_from)
    same_day = next((row for row in subscription.shares if row.effective_from == effective_from), None)
    if same_day is not None:
        same_day.company_share_percent, same_day.note = percent, note
        same_day.created_by_id = actor.id if actor is not None else None
    else:
        subscription.shares.append(
            EnrollmentShare(
                company_share_percent=percent,
                effective_from=effective_from,
                note=note,
                created_by_id=actor.id if actor is not None else None,
            )
        )
    db.flush()
    sync_installments(db, subscription)
    if notify and previous != percent:
        share_changed(db, subscription, percent, effective_from, actor)


def start_share(db: Session, subscription: Subscription, percent: int, actor: User | None) -> None:
    """The enrollment's first split, from its join day (recorded for every
    corporate enrollment, so its history is complete)."""
    subscription.shares = [
        EnrollmentShare(
            company_share_percent=percent,
            effective_from=subscription.joined_on,
            note="enrollment",
            created_by_id=actor.id if actor is not None else None,
        )
    ]
    db.flush()


def share_effective_from(requested: date | None, actor_is_company: bool) -> date:
    """Default today; a company changes its employees' split from today on
    (the staff records the company's word for any date)."""
    today = clock.today()
    effective_from = requested or today
    if actor_is_company and effective_from < today:
        raise HTTPException(status_code=409, detail="A new split applies from today on")
    return effective_from


# --- the company's statement ---------------------------------------------------------------


def months_between(start: str, end: str) -> list[str]:
    first, last = parse_period(start), parse_period(end)
    if first is None or last is None or first[0] > last[0]:
        raise HTTPException(status_code=400, detail="start and end must be YYYY-MM, start not after end")
    result, year, month = [], first[0].year, first[0].month
    while (year, month) <= (last[0].year, last[0].month):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def default_range(today: date | None = None) -> tuple[str, str]:
    """The current year, January to December."""
    today = today or clock.today()
    return f"{today.year:04d}-01", f"{today.year:04d}-12"


def _month(period: str, totals_list) -> StatementMonth:
    month = StatementMonth(
        period=period,
        employees=0,
        installments=0,
        fees_cents=0,
        company_share_cents=0,
        employee_share_cents=0,
        shortfall_cents=0,
        company_cents=0,
        company_paid_cents=0,
        company_open_cents=0,
        employee_paid_cents=0,
        employee_open_cents=0,
    )
    for totals in totals_list:
        month.employees += totals.employees
        month.installments += totals.installments
        month.fees_cents += totals.amount_cents
        month.company_share_cents += totals.company_share_cents
        month.employee_share_cents += totals.employee_share_cents
        month.shortfall_cents += totals.shortfall_cents
        month.company_cents += totals.company_cents
        month.company_paid_cents += totals.company_fees_paid_cents + (
            totals.shortfall_cents if totals.shortfall_paid_on is not None else 0
        )
        month.company_open_cents += totals.company_open_cents
        month.employee_paid_cents += totals.employee_paid_cents
        month.employee_open_cents += totals.employee_open_cents
    return month


def _add(total: StatementMonth, month: StatementMonth) -> None:
    for key in (
        "installments",
        "fees_cents",
        "company_share_cents",
        "employee_share_cents",
        "shortfall_cents",
        "company_cents",
        "company_paid_cents",
        "company_open_cents",
        "employee_paid_cents",
        "employee_open_cents",
    ):
        setattr(total, key, getattr(total, key) + getattr(month, key))


def company_statement(db: Session, partner, start: str, end: str, agreement_id: int | None = None) -> CompanyStatementOut:
    """The company's statement for the months ``start``..``end`` (by the
    month the fees are due): what it pays and what its employees pay."""
    from .partners import agreement_query

    periods = months_between(start, end)
    wanted = set(periods)
    query = agreement_query(db).filter(Agreement.partner_id == partner.id)
    if agreement_id is not None:
        query = query.filter(Agreement.id == agreement_id)
    agreements = query.all()
    books = ledgers(db, [row.id for row in agreements])
    today = clock.today()
    per_month: dict[str, list] = defaultdict(list)
    people: dict[int, dict] = {}
    for agreement in agreements:
        book = books[agreement.id]
        charges = {charge.period: charge for charge in agreement.charges}
        for period in book.periods():
            if period not in wanted:
                continue
            lines = book.lines(period)
            per_month[period].append(
                invoice_totals(period, lines, today, agreement.min_employees, agreement.payer, charges.get(period))
            )
            for subscription, item in lines:
                person = people.setdefault(
                    subscription.customer_id,
                    {
                        "customer": subscription.customer,
                        "agreements": set(),
                        "plans": set(),
                        "months": set(),
                        "latest": subscription,
                        "sums": defaultdict(int),
                    },
                )
                person["agreements"].add(agreement.name)
                person["plans"].add(subscription.modality.name)
                person["months"].add(period)
                if (subscription.joined_on, subscription.id) > (person["latest"].joined_on, person["latest"].id):
                    person["latest"] = subscription
                sums = person["sums"]
                company, employee = part_cents(item, PART_COMPANY), part_cents(item, PART_EMPLOYEE)
                sums["fees"] += item.amount_cents
                sums["company"] += company
                sums["employee"] += employee
                sums["company_paid" if item.company_paid_on is not None else "company_open"] += company
                sums["employee_paid" if item.employee_paid_on is not None else "employee_open"] += employee
    months = [_month(period, per_month.get(period, [])) for period in periods]
    totals = _month("total", [])
    for month in months:
        _add(totals, month)
    totals.employees = len(people)
    employees = [
        StatementEmployee(
            customer_id=customer_id,
            name=person["customer"].name,
            document=person["customer"].document or "",
            agreements=sorted(person["agreements"]),
            plans=sorted(person["plans"]),
            company_share_percent=current_share(person["latest"], today),
            months=len(person["months"]),
            fees_cents=person["sums"]["fees"],
            company_share_cents=person["sums"]["company"],
            employee_share_cents=person["sums"]["employee"],
            company_paid_cents=person["sums"]["company_paid"],
            company_open_cents=person["sums"]["company_open"],
            employee_paid_cents=person["sums"]["employee_paid"],
            employee_open_cents=person["sums"]["employee_open"],
        )
        for customer_id, person in people.items()
    ]
    employees.sort(key=lambda row: row.name.lower())
    return CompanyStatementOut(
        partner_id=partner.id,
        partner_name=partner.name,
        agreement_id=agreement_id,
        start=periods[0],
        end=periods[-1],
        totals=totals,
        months=months,
        employees=employees,
    )


def company_statement_csv(db: Session, partner, start: str, end: str, agreement_id: int | None = None) -> str:
    """One row per employee fee of the range: the payroll list (who pays
    what, month by month, and whether each part is paid)."""
    from .partners import agreement_query

    periods = set(months_between(start, end))
    query = agreement_query(db).filter(Agreement.partner_id == partner.id)
    if agreement_id is not None:
        query = query.filter(Agreement.id == agreement_id)
    agreements = query.all()
    books = ledgers(db, [row.id for row in agreements])
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "month",
            "agreement",
            "employee",
            "cpf",
            "plan",
            "installment",
            "due_on",
            "fee_cents",
            "company_percent",
            "company_cents",
            "company_paid_on",
            "employee_cents",
            "employee_paid_on",
        ]
    )
    rows = []
    for agreement in agreements:
        book = books[agreement.id]
        for period in book.periods():
            if period not in periods:
                continue
            for subscription, item in book.lines(period):
                company = part_cents(item, PART_COMPANY)
                percent = round(company * 100 / item.amount_cents) if item.amount_cents else 0
                rows.append(
                    [
                        period,
                        agreement.name,
                        subscription.customer.name,
                        subscription.customer.document or "",
                        subscription.modality.name,
                        item.sequence,
                        item.due_on.isoformat(),
                        item.amount_cents,
                        percent,
                        company,
                        item.company_paid_on.isoformat() if item.company_paid_on else "",
                        item.amount_cents - company,
                        item.employee_paid_on.isoformat() if item.employee_paid_on else "",
                    ]
                )
    rows.sort(key=lambda row: (row[0], row[2].lower(), row[6]))
    writer.writerows(rows)
    return buffer.getvalue()


# --- the employee's statement -----------------------------------------------------------------


def employee_statement(db: Session, customer: Customer, start: str, end: str) -> EmployeeStatementOut:
    """The customer's own part of every fee due in the months
    ``start``..``end`` (all of it for an individual plan, its share in a
    corporate co-payment, nothing when its company pays) and what its
    company paid."""
    periods = set(months_between(start, end))
    today = clock.today()
    rows = (
        db.query(Subscription)
        .options(
            joinedload(Subscription.modality),
            joinedload(Subscription.agreement).joinedload(Agreement.partner),
            selectinload(Subscription.installments),
        )
        .filter(Subscription.customer_id == customer.id)
        .all()
    )
    lines = []
    for subscription in rows:
        split = is_split(subscription)
        for item in subscription.installments:
            if invoice_period(item.due_on) not in periods:
                continue
            company = part_cents(item, PART_COMPANY) if split else 0
            own = item.amount_cents - company
            own_paid_on = item.employee_paid_on if split else item.paid_on
            own_paid_cents = item.employee_paid_cents if split else item.paid_cents
            if own == 0:
                own_status = "none"
            elif own_paid_on is not None:
                own_status = "paid"
            elif item.due_on < today:
                own_status = INSTALLMENT_OVERDUE
            else:
                own_status = "open"
            lines.append(
                EmployeeStatementLine(
                    subscription_id=subscription.id,
                    modality_name=subscription.modality.name,
                    partner_name=subscription.agreement.partner.name if subscription.agreement else None,
                    agreement_name=subscription.agreement.name if subscription.agreement else None,
                    sequence=item.sequence,
                    period_start=item.period_start,
                    period_end=item.period_end,
                    due_on=item.due_on,
                    amount_cents=item.amount_cents,
                    discount_cents=item.discount_cents or 0,
                    company_cents=company,
                    own_cents=own,
                    own_paid_on=own_paid_on,
                    own_paid_cents=own_paid_cents,
                    own_status=own_status,
                    company_paid_on=item.company_paid_on if split else None,
                    status=installment_status(item, today),
                )
            )
    lines.sort(key=lambda line: (line.due_on, line.subscription_id))
    totals = EmployeeStatementTotals(
        fees_cents=sum(line.amount_cents for line in lines),
        company_cents=sum(line.company_cents for line in lines),
        own_cents=sum(line.own_cents for line in lines),
        own_paid_cents=sum(line.own_paid_cents or 0 for line in lines if line.own_status == "paid"),
        own_open_cents=sum(line.own_cents for line in lines if line.own_status in ("open", INSTALLMENT_OVERDUE)),
        own_overdue_cents=sum(line.own_cents for line in lines if line.own_status == INSTALLMENT_OVERDUE),
    )
    first, last = sorted(periods)[0], sorted(periods)[-1]
    return EmployeeStatementOut(
        customer_id=customer.id, customer_name=customer.name, start=first, end=last, totals=totals, lines=lines
    )

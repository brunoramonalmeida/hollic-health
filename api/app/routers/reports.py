"""Management reports.

* ``/reports/appointments`` and ``/reports/plans`` carry money (balance
  reports): admins only.
* ``/reports/attendants`` measures staff activity: admins and supervisors.
* ``/reports/commissions`` (and ``/{sellerId}``, the statement) gives the
  plans sold by sellers, their portfolio and the commission due on the fees
  received: admins see everyone, with the filters ``region_id`` and
  ``seller_id``; a seller login sees only itself.
* ``/reports/payouts`` controls what is due to each professional (signed,
  i.e. completed, appointments only): admins see everyone, with the filters
  ``name``, ``specialty_id``, ``clinic_id``, ``professional_id``; a
  professional login sees only its own payout.

All accept ``granularity=week|month``, ``start``/``end`` dates (default: last
8 weeks or 6 months) and, where it applies, ``professional_id``/``clinic_id``.
"""

import csv
import io
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..auth import (
    ROLE_ADMIN,
    ROLE_ATTENDANT,
    ROLE_CLIENT,
    ROLE_PROFESSIONAL,
    ROLE_SELLER,
    ROLE_SUPERVISOR,
    is_professional,
    is_seller,
    require_roles,
)
from ..copayment import default_range as default_statement_range, employee_statement
from ..auth import get_current_user
from ..billing import (
    receipts,
    INSTALLMENT_OVERDUE,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_OVERDUE,
    STATUS_PENDING,
    installment_status,
    subscription_status,
)
from ..database import get_db
from ..models import (
    AgreementCharge,
    Appointment,
    Customer,
    Installment,
    SaleOverride,
    Modality,
    PayoutPayment,
    PayoutSettlement,
    Professional,
    Region,
    Seller,
    Subscription,
    User,
)
from ..partners import agreement_query, ledgers
from ..pricing import remote_value
from ..sales import commission_cents
from .subscriptions import organisation_ids
from ..reports import (
    GRANULARITIES,
    MONTH,
    WEEK,
    Bucket,
    CommissionStats,
    commission_stats,
    commissions_breakdown,
    commissions_by_period,
    TaxedService,
    agreement_stats,
    group_lines,
    jurisdiction_key,
    jurisdiction_label,
    matches_professional,
    payout_cents,
    payout_stats,
    payouts_by_period,
    payouts_by_professional,
    tax_totals,
    taxes_by_period,
    appointments_breakdown,
    appointments_by_period,
    attendance_breakdown,
    attendance_by_period,
    attendance_stats,
    attendant_stats,
    attendants_by_period,
    buckets_between,
    default_range,
    plan_stats,
    plans_breakdown,
    plans_by_period,
    subscription_counts,
)
from ..payouts import PAYEE_CLINIC, PAYEE_PROFESSIONAL, payment_totals, position
from ..taxation import get_company
from .taxes import _company_out
from ..categories import is_enterprise
from ..schemas import (
    AgreementSalesRow,
    CategorySalesRow,
    EmployeeStatementOut,
    AppointmentsReport,
    CommissionPeriod,
    CommissionRow,
    CommissionsReport,
    CommissionStatement,
    CommissionStatementLine,
    Portfolio,
    AttendancePeriod,
    AttendanceReport,
    FinancePeriod,
    FinanceReport,
    TaxBucket,
    TaxPeriod,
    TaxesReport,
    AttendantPeriod,
    AttendantRow,
    AttendantsReport,
    BreakdownRow,
    PayoutPeriod,
    PayoutStatement,
    PayoutStatementLine,
    PayoutsReport,
    ProfessionalPayoutRow,
    PeriodAmounts,
    PlanPeriod,
    PlanSalesRow,
    PlansReport,
)

router = APIRouter(prefix="/reports", tags=["reports"])

ADMIN = [Depends(require_roles(ROLE_ADMIN))]
MANAGERS = [Depends(require_roles(ROLE_ADMIN, ROLE_SUPERVISOR))]
PAYOUT_VIEWERS = [Depends(require_roles(ROLE_ADMIN, ROLE_PROFESSIONAL))]
COMMISSION_VIEWERS = [Depends(require_roles(ROLE_ADMIN, ROLE_SELLER))]
MAX_DAYS = 731


def _range(granularity: str, start: date | None, end: date | None) -> tuple[date, date]:
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=400, detail="granularity must be week or month")
    default_start, default_end = default_range(granularity, clock.today())
    start = start or default_start
    end = end or default_end
    if end < start:
        raise HTTPException(status_code=400, detail="end must not be before start")
    if (end - start).days > MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"Range limited to {MAX_DAYS} days")
    return start, end


def _span(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _appointments(db: Session, start: date, end: date, professional_id, clinic_id, by="marked_at"):
    column = Appointment.marked_at if by == "marked_at" else Appointment.created_at
    lower, upper = _span(start, end)
    query = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.professional),
            joinedload(Appointment.clinic),
            joinedload(Appointment.specialty),
            joinedload(Appointment.modality),
            joinedload(Appointment.created_by),
        )
        .filter(column >= lower, column < upper)
    )
    if professional_id is not None:
        query = query.filter(Appointment.professional_id == professional_id)
    if clinic_id is not None:
        query = query.filter(Appointment.clinic_id == clinic_id)
    return query.all()


def _subscriptions(db: Session):
    return (
        db.query(Subscription)
        .options(
            joinedload(Subscription.modality),
            joinedload(Subscription.created_by),
            selectinload(Subscription.installments),
        )
        .all()
    )


def received_between(start: date, end: date):
    """Installments with money received in the range: the fee, or a part of a corporate fee."""
    return or_(
        and_(Installment.paid_on >= start, Installment.paid_on <= end),
        and_(Installment.company_paid_on >= start, Installment.company_paid_on <= end),
        and_(Installment.employee_paid_on >= start, Installment.employee_paid_on <= end),
    )


def _paid_installments(db: Session, start: date, end: date):
    return db.query(Installment).options(joinedload(Installment.registered_by)).filter(received_between(start, end)).all()


def _taxed_services(db: Session, start: date, end: date, professional_id, clinic_id, basis: str) -> list[TaxedService]:
    """Completed appointments (or all booked ones when basis=all) by service
    date plus installments paid in the range."""
    statuses = ["realizada"] if basis == "completed" else ["realizada", "agendada"]
    lower, upper = _span(start, end)
    query = (
        db.query(Appointment)
        .options(selectinload(Appointment.taxes))
        .filter(Appointment.marked_at >= lower, Appointment.marked_at < upper, Appointment.status.in_(statuses))
    )
    if professional_id is not None:
        query = query.filter(Appointment.professional_id == professional_id)
    if clinic_id is not None:
        query = query.filter(Appointment.clinic_id == clinic_id)
    services = [
        TaxedService("appointment", row.marked_at.date(), row.amount_cents, tuple(row.taxes)) for row in query.all()
    ]
    if professional_id is None and clinic_id is None:
        paid = (
            db.query(Installment)
            .options(selectinload(Installment.taxes))
            .filter(Installment.paid_on >= start, Installment.paid_on <= end)
            .all()
        )
        services += [TaxedService("subscription", item.paid_on, item.paid_cents or 0, tuple(item.taxes)) for item in paid]
        # Shortfalls of corporate agreements: plan-fee revenue billed at the head office.
        charges = (
            db.query(AgreementCharge)
            .options(selectinload(AgreementCharge.taxes))
            .filter(AgreementCharge.paid_on >= start, AgreementCharge.paid_on <= end)
            .all()
        )
        services += [TaxedService("subscription", item.paid_on, item.amount_cents, tuple(item.taxes)) for item in charges]
    return services


def _tax_buckets(groups) -> list[TaxBucket]:
    return [TaxBucket(key=g.key, label=g.label, services=g.services, base_cents=g.base_cents, tax_cents=g.tax_cents) for g in groups]


def _tax_period(bucket, totals) -> TaxPeriod:
    return TaxPeriod(
        period=bucket.key if hasattr(bucket, "key") else "total",
        start=bucket.start,
        end=bucket.end,
        services=totals.services,
        amount_cents=totals.amount_cents,
        tax_cents=totals.tax_cents,
        net_cents=totals.net_cents,
        by_code=_tax_buckets(sorted(totals.by_code.values(), key=lambda g: (-g.tax_cents, g.label))),
    )


@router.get("/taxes", response_model=TaxesReport, dependencies=ADMIN)
def taxes_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    professional_id: int | None = None,
    clinic_id: int | None = None,
    basis: str = Query(default="completed", description="completed: services rendered; all: include scheduled"),
    db: Session = Depends(get_db),
):
    """Taxes owed per period, tax code, level and jurisdiction. Plan fees are
    included only when no professional/clinic filter is set."""
    if basis not in {"completed", "all"}:
        raise HTTPException(status_code=400, detail="basis must be completed or all")
    start, end = _range(granularity, start, end)
    services = _taxed_services(db, start, end, professional_id, clinic_id, basis)
    whole = type("Span", (), {"start": start, "end": end})()
    return TaxesReport(
        granularity=granularity,
        start=start,
        end=end,
        basis=basis,
        totals=_tax_period(whole, tax_totals(services)),
        periods=[_tax_period(bucket, totals) for bucket, totals in taxes_by_period(services, start, end, granularity)],
        by_code=_tax_buckets(group_lines(services, lambda l, s: l.code, lambda l, s: f"{l.name} ({l.code})")),
        by_level=_tax_buckets(group_lines(services, lambda l, s: l.level, lambda l, s: l.level.capitalize())),
        by_jurisdiction=_tax_buckets(group_lines(services, jurisdiction_key, jurisdiction_label)),
        by_source=_tax_buckets(
            group_lines(services, lambda l, s: s.source, lambda l, s: "Appointments" if s.source == "appointment" else "Plan fees")
        ),
    )


@router.get("/taxes/ledger.csv", dependencies=ADMIN)
def taxes_ledger(
    start: date | None = None,
    end: date | None = None,
    basis: str = Query(default="completed"),
    db: Session = Depends(get_db),
):
    """One CSV row per tax line, for the accountant."""
    start, end = _range("month", start, end)
    services = _taxed_services(db, start, end, None, None, basis)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["date", "source", "code", "tax", "level", "state", "city", "ibge_code", "rate_percent", "base_cents", "tax_cents", "service_amount_cents"])
    for service in sorted(services, key=lambda s: (s.on, s.source)):
        for line in service.lines:
            writer.writerow(
                [
                    service.on.isoformat(),
                    service.source,
                    line.code,
                    line.name,
                    line.level,
                    line.state or "",
                    line.city or "",
                    line.ibge_code or "",
                    f"{line.rate_bp / 100:.2f}",
                    line.base_cents,
                    line.amount_cents,
                    service.amount_cents,
                ]
            )
    buffer.seek(0)
    filename = f"taxes_{start.isoformat()}_{end.isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _finance_period(bucket, appointments, installments, payouts=(), charges=()) -> FinancePeriod:
    completed = [row for row in appointments if bucket.start <= row.marked_at.date() <= bucket.end]
    # Plan fees received by date (each part of a corporate fee on its own date);
    # their taxes are due once the fee is settled (both parts in).
    received = [(day, cents) for item in installments for day, cents in receipts(item) if bucket.start <= day <= bucket.end]
    paid = [item for item in installments if item.paid_on is not None and bucket.start <= item.paid_on <= bucket.end]
    # Shortfalls paid by companies below their agreement's minimum: plan-fee revenue too.
    shortfalls = [item for item in charges if bucket.start <= item.paid_on <= bucket.end]
    appt_amount = sum(row.amount_cents for row in completed)
    over_limit = [row for row in completed if row.over_limit]
    charges_cents = sum(item.amount_cents for item in shortfalls)
    plan_revenue = sum(cents for _, cents in received) + charges_cents
    tax = (
        sum(row.tax_cents for row in completed)
        + sum(item.tax_cents or 0 for item in paid)
        + sum(item.tax_cents or 0 for item in shortfalls)
    )
    handed = [p for p in payouts if p.paid_on is not None and bucket.start <= p.paid_on <= bucket.end]
    to_professionals = sum(p.amount_cents for p in handed if p.payee_type == PAYEE_PROFESSIONAL)
    to_clinics = sum(p.amount_cents for p in handed if p.payee_type == PAYEE_CLINIC)
    net = appt_amount + plan_revenue - tax
    return FinancePeriod(
        period=getattr(bucket, "key", "total"),
        start=bucket.start,
        end=bucket.end,
        appointments_completed=len(completed),
        appointments_gross_cents=sum(row.gross_cents for row in completed),
        appointments_discount_cents=sum(row.discount_cents for row in completed),
        appointments_amount_cents=appt_amount,
        appointments_over_limit=len(over_limit),
        appointments_over_limit_cents=sum(row.amount_cents for row in over_limit),
        plan_payments=len(received),
        plan_revenue_cents=plan_revenue,
        agreement_charges=len(shortfalls),
        agreement_charges_cents=charges_cents,
        revenue_cents=appt_amount + plan_revenue,
        tax_cents=tax,
        net_cents=net,
        payouts_professionals_cents=to_professionals,
        payouts_clinics_cents=to_clinics,
        payouts_cents=to_professionals + to_clinics,
        result_cents=net - to_professionals - to_clinics,
    )


@router.get("/finance", response_model=FinanceReport, dependencies=ADMIN)
def finance_report(
    granularity: str = Query(default="month"),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    """Revenue actually earned (completed appointments + plan fees received),
    discounts granted, taxes owed, net, the payouts handed to professionals
    and clinics (advances and installments, by payment date) and the result."""
    start, end = _range(granularity, start, end)
    lower, upper = _span(start, end)
    appointments = (
        db.query(Appointment)
        .filter(Appointment.marked_at >= lower, Appointment.marked_at < upper, Appointment.status == "realizada")
        .all()
    )
    installments = db.query(Installment).filter(received_between(start, end)).all()
    payouts = db.query(PayoutPayment).filter(PayoutPayment.paid_on >= start, PayoutPayment.paid_on <= end).all()
    charges = db.query(AgreementCharge).filter(AgreementCharge.paid_on >= start, AgreementCharge.paid_on <= end).all()
    whole = type("Span", (), {"start": start, "end": end})()
    company = get_company(db)
    db.commit()
    return FinanceReport(
        granularity=granularity,
        start=start,
        end=end,
        company=_company_out(company),
        totals=_finance_period(whole, appointments, installments, payouts, charges),
        periods=[
            _finance_period(bucket, appointments, installments, payouts, charges)
            for bucket in buckets_between(start, end, granularity)
        ],
    )


def _period_amounts(bucket, amounts) -> PeriodAmounts:
    return PeriodAmounts(
        period=bucket.key,
        start=bucket.start,
        end=bucket.end,
        scheduled=amounts.scheduled,
        completed=amounts.completed,
        cancelled=amounts.cancelled,
        gross_cents=amounts.gross_cents,
        discount_cents=amounts.discount_cents,
        amount_cents=amounts.amount_cents,
        over_limit=amounts.over_limit,
        over_limit_cents=amounts.over_limit_cents,
        on_call=amounts.on_call,
        on_call_cents=amounts.on_call_cents,
        urgent=amounts.urgent,
        urgent_cents=amounts.urgent_cents,
        remote=amounts.remote,
        remote_cents=amounts.remote_cents,
    )


def _breakdown(rows, key, name) -> list[BreakdownRow]:
    return [
        BreakdownRow(
            id=item_id,
            name=label,
            appointments=amounts.appointments,
            completed=amounts.completed,
            cancelled=amounts.cancelled,
            gross_cents=amounts.gross_cents,
            discount_cents=amounts.discount_cents,
            amount_cents=amounts.amount_cents,
            over_limit=amounts.over_limit,
            over_limit_cents=amounts.over_limit_cents,
            on_call=amounts.on_call,
            on_call_cents=amounts.on_call_cents,
            urgent=amounts.urgent,
            urgent_cents=amounts.urgent_cents,
            remote=amounts.remote,
            remote_cents=amounts.remote_cents,
        )
        for item_id, label, amounts in appointments_breakdown(rows, key, name)
    ]


@router.get("/appointments", response_model=AppointmentsReport, dependencies=ADMIN)
def appointments_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    professional_id: int | None = None,
    clinic_id: int | None = None,
    db: Session = Depends(get_db),
):
    start, end = _range(granularity, start, end)
    rows = _appointments(db, start, end, professional_id, clinic_id)
    periods = appointments_by_period(rows, start, end, granularity)
    totals = PeriodAmounts(
        period="total",
        start=start,
        end=end,
        scheduled=sum(a.scheduled for _, a in periods),
        completed=sum(a.completed for _, a in periods),
        cancelled=sum(a.cancelled for _, a in periods),
        gross_cents=sum(a.gross_cents for _, a in periods),
        discount_cents=sum(a.discount_cents for _, a in periods),
        amount_cents=sum(a.amount_cents for _, a in periods),
        over_limit=sum(a.over_limit for _, a in periods),
        over_limit_cents=sum(a.over_limit_cents for _, a in periods),
        on_call=sum(a.on_call for _, a in periods),
        on_call_cents=sum(a.on_call_cents for _, a in periods),
        urgent=sum(a.urgent for _, a in periods),
        urgent_cents=sum(a.urgent_cents for _, a in periods),
        remote=sum(a.remote for _, a in periods),
        remote_cents=sum(a.remote_cents for _, a in periods),
    )
    return AppointmentsReport(
        granularity=granularity,
        start=start,
        end=end,
        totals=totals,
        periods=[_period_amounts(bucket, amounts) for bucket, amounts in periods],
        by_professional=_breakdown(rows, lambda r: r.professional_id, lambda r: r.professional.name),
        by_clinic=_breakdown(rows, lambda r: r.clinic_id, lambda r: r.clinic.name),
        by_specialty=_breakdown(rows, lambda r: r.specialty_id, lambda r: r.specialty.name),
        by_plan=_breakdown(
            rows, lambda r: r.modality_id or 0, lambda r: r.modality.name if r.modality else "No plan"
        ),
    )


def _attendance_period(key, start, end, stats) -> AttendancePeriod:
    return AttendancePeriod(
        period=key,
        start=start,
        end=end,
        scheduled=stats.scheduled,
        checked_in=stats.checked_in,
        checked_out=stats.checked_out,
        completed_without_checkin=stats.completed_without_checkin,
        no_shows=stats.no_shows,
        awaiting=stats.awaiting,
        cancelled=stats.cancelled,
        attendance_rate=stats.attendance_rate,
        avg_arrival_minutes=stats.avg_arrival_minutes,
        avg_visit_minutes=stats.avg_visit_minutes,
    )


@router.get("/attendance", response_model=AttendanceReport, dependencies=MANAGERS)
def attendance_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    professional_id: int | None = None,
    clinic_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Attendance audit: check-ins, check-outs, completions without check-in,
    no-shows, punctuality and visit duration, by period, clinic and professional."""
    start, end = _range(granularity, start, end)
    rows = _appointments(db, start, end, professional_id, clinic_id)
    now = clock.now()
    return AttendanceReport(
        granularity=granularity,
        start=start,
        end=end,
        totals=_attendance_period("total", start, end, attendance_stats(rows, now)),
        periods=[_attendance_period(b.key, b.start, b.end, s) for b, s in attendance_by_period(rows, start, end, granularity, now)],
        by_clinic=[_attendance_period(name, start, end, s) for name, s in attendance_breakdown(rows, None, lambda r: r.clinic.name, now)],
        by_professional=[
            _attendance_period(name, start, end, s) for name, s in attendance_breakdown(rows, None, lambda r: r.professional.name, now)
        ],
    )


def _attendant_rows(stats) -> list[AttendantRow]:
    return [
        AttendantRow(
            user_id=item.user_id,
            name=item.name,
            role=item.role,
            appointments_booked=item.appointments_booked,
            appointments_completed=item.appointments_completed,
            appointments_cancelled=item.appointments_cancelled,
            completion_rate=item.completion_rate,
            plans_sold=item.plans_sold,
            payments_registered=item.payments_registered,
            payments_cents=item.payments_cents,
        )
        for item in stats
    ]


@router.get("/attendants", response_model=AttendantsReport, dependencies=MANAGERS)
def attendants_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    professional_id: int | None = None,
    clinic_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Activity per user: appointments booked (by booking date), completion
    rate of those, plans sold and payments registered."""
    start, end = _range(granularity, start, end)
    appointments = _appointments(db, start, end, professional_id, clinic_id, by="created_at")
    lower, upper = _span(start, end)
    subscriptions = [row for row in _subscriptions(db) if lower <= row.created_at < upper]
    installments = _paid_installments(db, start, end)
    return AttendantsReport(
        granularity=granularity,
        start=start,
        end=end,
        totals=_attendant_rows(attendant_stats(appointments, subscriptions, installments)),
        periods=[
            AttendantPeriod(period=bucket.key, start=bucket.start, end=bucket.end, rows=_attendant_rows(stats))
            for bucket, stats in attendants_by_period(appointments, subscriptions, installments, start, end, granularity)
        ],
    )


def _plan_period(bucket, stats) -> PlanPeriod:
    return PlanPeriod(
        period=bucket.key,
        start=bucket.start,
        end=bucket.end,
        sold=stats.sold,
        cancelled=stats.cancelled,
        payments=stats.payments,
        revenue_cents=stats.revenue_cents,
        appointments_covered=stats.appointments_covered,
        appointments_over_limit=stats.appointments_over_limit,
        over_limit_cents=stats.over_limit_cents,
    )


@router.get("/plans", response_model=PlansReport, dependencies=ADMIN)
def plans_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    """Plans sold and cancelled, payments received, current subscription
    counts and, per plan, the appointments booked with it in the range:
    inside its appointment limit or over it (billed at the full price)."""
    start, end = _range(granularity, start, end)
    subscriptions = _subscriptions(db)
    installments = _paid_installments(db, start, end)
    appointments = _appointments(db, start, end, None, None)
    counts = subscription_counts(subscriptions, clock.today())
    totals = plan_stats(subscriptions, installments, start, end, appointments)
    limits = {row.id: row for row in db.query(Modality).all()}
    by_plan = _plan_rows(subscriptions, installments, start, end, appointments, limits)
    return PlansReport(
        granularity=granularity,
        start=start,
        end=end,
        active_subscriptions=counts.active,
        overdue_subscriptions=counts.overdue,
        pending_subscriptions=counts.pending,
        totals=PlanPeriod(
            period="total",
            start=start,
            end=end,
            sold=totals.sold,
            cancelled=totals.cancelled,
            payments=totals.payments,
            revenue_cents=totals.revenue_cents,
            appointments_covered=totals.appointments_covered,
            appointments_over_limit=totals.appointments_over_limit,
            over_limit_cents=totals.over_limit_cents,
        ),
        periods=[
            _plan_period(bucket, stats)
            for bucket, stats in plans_by_period(subscriptions, installments, start, end, granularity, appointments)
        ],
        by_plan=by_plan,
        by_category=_category_rows(by_plan),
        by_agreement=_agreement_rows(db, subscriptions, start, end),
    )


def _plan_rows(subscriptions, installments, start, end, appointments, plans: dict) -> list[PlanSalesRow]:
    """One row per plan (``plans``: id -> Modality, for its allowance and category)."""
    rows = []
    for modality_id, name, stats in plans_breakdown(subscriptions, installments, start, end, appointments):
        plan = plans.get(modality_id)
        rows.append(
            PlanSalesRow(
                modality_id=modality_id,
                name=name,
                category_id=plan.category_id if plan else None,
                category_name=plan.category.name if plan and plan.category else None,
                enterprise=is_enterprise(plan) if plan else False,
                sold=stats.sold,
                cancelled=stats.cancelled,
                payments=stats.payments,
                revenue_cents=stats.revenue_cents,
                appointment_limit=plan.appointment_limit if plan else 0,
                appointment_limit_period=(plan.appointment_limit_period or "month") if plan else "month",
                appointments_covered=stats.appointments_covered,
                appointments_over_limit=stats.appointments_over_limit,
                over_limit_cents=stats.over_limit_cents,
            )
        )
    return rows


def _category_rows(rows: list[PlanSalesRow]) -> list[CategorySalesRow]:
    """The plans' figures grouped by their category (the plans without one together)."""
    groups: dict = {}
    for row in rows:
        group = groups.setdefault(
            row.category_id,
            CategorySalesRow(
                category_id=row.category_id,
                name=row.category_name or "No category",
                enterprise=row.enterprise,
                plans=0,
                sold=0,
                cancelled=0,
                payments=0,
                revenue_cents=0,
            ),
        )
        group.plans += 1
        for key in ("sold", "cancelled", "payments", "revenue_cents", "appointments_covered", "appointments_over_limit"):
            setattr(group, key, getattr(group, key) + getattr(row, key))
    return sorted(groups.values(), key=lambda group: (-group.revenue_cents, group.name.lower()))


def _agreement_rows(db: Session, subscriptions, start: date, end: date) -> list[AgreementSalesRow]:
    """Every corporate agreement (also those with nothing in the range, so
    the control is complete), with its headcount this month against the
    minimum, its enrollments, fees received, negotiated discount granted and
    shortfalls paid in the range, and the shortfalls still open today."""
    from .partners import agreement_out  # the agreement's figures, as its page shows them

    books = ledgers(db)
    result = []
    for agreement in agreement_query(db).all():
        book = books[agreement.id]
        figures = agreement_out(agreement, book)
        stats = agreement_stats(book.enrollments, start, end)
        paid = [charge for charge in agreement.charges if start <= charge.paid_on <= end]
        result.append(
            AgreementSalesRow(
                agreement_id=agreement.id,
                agreement_name=agreement.name,
                partner_id=agreement.partner_id,
                partner_name=agreement.partner.name,
                payer=agreement.payer,
                min_employees=agreement.min_employees,
                employees_month=figures.employees,
                conditions_met=figures.conditions_met,
                enrolled=stats.enrolled,
                cancelled=stats.cancelled,
                payments=stats.payments,
                revenue_cents=stats.revenue_cents,
                discount_cents=stats.discount_cents,
                shortfall_cents=sum(charge.amount_cents for charge in paid),
                shortfall_open_cents=figures.shortfall_open_cents,
                company_paid_cents=stats.company_paid_cents,
                employee_paid_cents=stats.employee_paid_cents,
            )
        )
    return sorted(result, key=lambda item: (-item.revenue_cents, -item.enrolled, item.partner_name.lower(), item.agreement_name.lower()))


# --- seller commissions -----------------------------------------------------------


def _commission_period(bucket, stats) -> CommissionPeriod:
    return CommissionPeriod(
        period=getattr(bucket, "key", "total"),
        start=bucket.start,
        end=bucket.end,
        sold=stats.sold,
        cancelled=stats.cancelled,
        payments=stats.payments,
        revenue_cents=stats.revenue_cents,
        discount_cents=stats.discount_cents,
        commission_cents=stats.commission_cents,
        team_sold=stats.team_sold,
        team_payments=stats.team_payments,
        team_revenue_cents=stats.team_revenue_cents,
        override_cents=stats.override_cents,
        total_cents=stats.total_cents,
    )


def _commission_row(stats: CommissionStats, **fields) -> CommissionRow:
    return CommissionRow(
        **fields,
        sold=stats.sold,
        cancelled=stats.cancelled,
        payments=stats.payments,
        revenue_cents=stats.revenue_cents,
        discount_cents=stats.discount_cents,
        commission_cents=stats.commission_cents,
        team_sold=stats.team_sold,
        team_payments=stats.team_payments,
        team_revenue_cents=stats.team_revenue_cents,
        override_cents=stats.override_cents,
        leader_override_cents=stats.leader_override_cents,
        total_cents=stats.total_cents,
    )


def portfolio(db: Session, rows, today: date) -> Portfolio:
    """The sales in ``rows`` as they stand today, plus the customers behind them."""
    result = Portfolio(subscriptions=len(rows))
    seller_ids = {row.seller_id for row in rows}
    customer_ids = {row.customer_id for row in rows}
    if seller_ids:
        customer_ids |= {
            row.id for row in db.query(Customer.id).filter(Customer.seller_id.in_(sorted(seller_ids))).all()
        }
    result.customers = len(customer_ids)
    week_end = today + timedelta(days=7)
    for row in rows:
        status = subscription_status(row, row.installments, today)
        if status == STATUS_ACTIVE:
            result.active += 1
            current = min(
                (item for item in row.installments if item.paid_on is None), key=lambda item: item.sequence, default=None
            )
            result.monthly_cents += current.amount_cents if current is not None else 0
        elif status == STATUS_PENDING:
            result.pending += 1
        elif status == STATUS_OVERDUE:
            result.overdue += 1
        elif status == STATUS_EXPIRED:
            result.expired += 1
        elif status == STATUS_CANCELLED:
            result.cancelled += 1
        if status in (STATUS_ACTIVE, STATUS_PENDING, STATUS_OVERDUE):
            for item in row.installments:
                if item.paid_on is None and today <= item.due_on < week_end:
                    result.installments_due_week += 1
                if installment_status(item, today) == INSTALLMENT_OVERDUE:
                    result.installments_overdue += 1
    return result


def _viewer_scope(db: Session, user: User, seller_id: int | None) -> tuple[int | None, set[int] | None]:
    """(seller asked, sellers the viewer may see). A seller login reports on
    itself and on every seller below it; the staff on everyone."""
    if not is_seller(user):
        return seller_id, None
    allowed = organisation_ids(db, user.seller_id)
    if seller_id is not None and seller_id not in allowed:
        raise HTTPException(status_code=403, detail="Not allowed for another seller")
    return seller_id, allowed


def _sales_of(db: Session, seller_ids: set[int] | None, region_id: int | None):
    """Sales sold by, or led by, the sellers (None: everyone's)."""
    query = (
        db.query(Subscription)
        .options(
            joinedload(Subscription.customer),
            joinedload(Subscription.modality),
            joinedload(Subscription.region),
            joinedload(Subscription.seller),
            selectinload(Subscription.installments),
            selectinload(Subscription.overrides).joinedload(SaleOverride.seller),
        )
        .filter(Subscription.seller_id.is_not(None))
    )
    if seller_ids is not None:
        ids = sorted(seller_ids)
        led = db.query(SaleOverride.subscription_id).filter(SaleOverride.seller_id.in_(ids))
        query = query.filter(Subscription.seller_id.in_(ids) | Subscription.id.in_(led))
    if region_id is not None:
        query = query.filter(Subscription.region_id == region_id)
    return query.all()


@router.get("/commissions", response_model=CommissionsReport, dependencies=COMMISSION_VIEWERS)
def commissions_report(
    granularity: str = Query(default=WEEK),
    start: date | None = None,
    end: date | None = None,
    region_id: int | None = None,
    seller_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Plans sold by sellers, their portfolio today, the commission due on the
    fees received and the override due to team leaders.

    Each paid installment (by payment date) of a subscription sold by a
    seller pays the commission snapshotted on the sale to the seller and the
    override snapshotted on it to the seller's leader. The seller rows carry
    both sides of each seller (its own sales, and the sales of its team with
    the override they pay it); the region rows group the sales by the region
    they were made in; the totals are the company's cost (every commission
    and every override) — unless the report is one seller's (``seller_id``,
    or a seller login), when they are that seller's earnings: its own
    commission plus its override on its team. A seller login sees itself
    and the collaborators it leads."""
    start, end = _range(granularity, start, end)
    seller_id, allowed = _viewer_scope(db, user, seller_id)
    focus = seller_id if seller_id is not None else (user.seller_id if is_seller(user) else None)
    rows = _sales_of(db, {seller_id} if seller_id is not None else allowed, region_id)
    totals = commission_stats(rows, start, end, focus)
    empty = CommissionStats()
    teams: dict[int, int] = {}
    for item in db.query(Seller.leader_id).filter(Seller.leader_id.is_not(None)).all():
        teams[item.leader_id] = teams.get(item.leader_id, 0) + 1

    seller_query = db.query(Seller).options(joinedload(Seller.region), joinedload(Seller.leader))
    if seller_id is not None:
        seller_query = seller_query.filter(Seller.id == seller_id)
    elif allowed is not None:
        seller_query = seller_query.filter(Seller.id.in_(sorted(allowed)))
    involved = {row.seller_id for row in rows} | {snap.seller_id for row in rows for snap in row.overrides}
    sellers = [
        item
        for item in seller_query.all()
        if item.id in involved or region_id is None or item.region_id == region_id
    ]
    seller_rows = [
        _commission_row(
            commission_stats(rows, start, end, item.id),
            id=item.id,
            name=item.name,
            region_id=item.region_id,
            region_name=item.region.name,
            active=bool(item.active),
            commission_bp=item.region.commission_bp or 0,
            leader_id=item.leader_id,
            leader_name=item.leader.name if item.leader is not None else None,
            team_size=teams.get(item.id, 0),
            organisation_size=len(organisation_ids(db, item.id)) - 1,
        )
        for item in sellers
    ]

    region_query = db.query(Region)
    if region_id is not None:
        region_query = region_query.filter(Region.id == region_id)
    if seller_id is not None or allowed is not None:
        # The sellers' current regions and the regions their sales were made in.
        regions_involved = {row.region_id for row in rows} | {item.region_id for item in sellers}
        region_query = region_query.filter(Region.id.in_(sorted(regions_involved)))
    seller_counts: dict[int, int] = {}
    for item in db.query(Seller).all():
        seller_counts[item.region_id] = seller_counts.get(item.region_id, 0) + 1
    by_region = commissions_breakdown(rows, lambda row: row.region_id, start, end)
    region_rows = [
        _commission_row(
            by_region.get(item.id, empty),
            id=item.id,
            name=item.name,
            region_id=item.id,
            region_name=item.name,
            commission_bp=item.commission_bp or 0,
            sellers_count=seller_counts.get(item.id, 0),
        )
        for item in region_query.all()
    ]

    def order(row: CommissionRow):
        return (-row.total_cents, -row.sold, row.name.lower())

    return CommissionsReport(
        granularity=granularity,
        start=start,
        end=end,
        portfolio=portfolio(db, [row for row in rows if focus is None or row.seller_id == focus], clock.today()),
        totals=_commission_period(Bucket("total", start, end), totals),
        periods=[_commission_period(bucket, stats) for bucket, stats in commissions_by_period(rows, start, end, granularity, focus)],
        by_seller=sorted(seller_rows, key=order),
        by_region=sorted(region_rows, key=order),
    )


@router.get("/commissions/{seller_id}", response_model=CommissionStatement, dependencies=COMMISSION_VIEWERS)
def commission_statement(
    seller_id: int,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every payment received in the range that pays the seller something —
    its commission on its own sales, its override on its team's — what
    backs paying the seller."""
    start, end = _range(MONTH, start, end)
    _viewer_scope(db, user, seller_id)
    seller = db.query(Seller).options(joinedload(Seller.region)).filter(Seller.id == seller_id).first()
    if seller is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    lines = []
    for row in _sales_of(db, {seller_id}, None):
        own = row.seller_id == seller_id
        snapshot = next((snap for snap in row.overrides if snap.seller_id == seller_id), None)
        if not own and snapshot is None:
            continue
        rate = (row.commission_bp or 0) if own else snapshot.rate_bp
        for item in row.installments:
            if item.paid_on is None or not (start <= item.paid_on <= end):
                continue
            lines.append(
                CommissionStatementLine(
                    installment_id=item.id,
                    subscription_id=row.id,
                    sequence=item.sequence,
                    kind="commission" if own else "override",
                    level=0 if own else snapshot.level,
                    sold_by=row.seller.name,
                    customer_name=row.customer.name,
                    modality_name=row.modality.name,
                    region_name=row.region.name if row.region is not None else None,
                    paid_on=item.paid_on,
                    paid_cents=item.paid_cents or 0,
                    discount_percent=row.discount_percent or 0,
                    commission_bp=rate,
                    commission_cents=commission_cents(item.paid_cents or 0, rate),
                )
            )
    lines.sort(key=lambda line: (line.paid_on, line.customer_name.lower(), line.sequence))
    commission = sum(line.commission_cents for line in lines if line.kind == "commission")
    override = sum(line.commission_cents for line in lines if line.kind == "override")
    return CommissionStatement(
        seller_id=seller.id,
        seller_name=seller.name,
        region_name=seller.region.name,
        start=start,
        end=end,
        payments=len(lines),
        revenue_cents=sum(line.paid_cents for line in lines),
        commission_cents=commission,
        override_cents=override,
        total_cents=commission + override,
        lines=lines,
    )


# --- professional payouts ---------------------------------------------------------


def _professionals(db: Session, name: str | None, specialty_id: int | None, clinic_id: int | None, professional_id: int | None):
    query = db.query(Professional).options(joinedload(Professional.specialty), joinedload(Professional.clinic))
    if professional_id is not None:
        query = query.filter(Professional.id == professional_id)
    rows = [row for row in query.all() if matches_professional(row, name, specialty_id, clinic_id)]
    return sorted(rows, key=lambda row: row.name.lower())


def _payout_appointments(db: Session, start: date, end: date, professional_ids: list[int]):
    """Every appointment of the professionals in the range, by service date
    (the aggregation decides what is paid)."""
    if not professional_ids:
        return []
    lower, upper = _span(start, end)
    return (
        db.query(Appointment)
        .options(
            joinedload(Appointment.customer),
            joinedload(Appointment.dependent),
            joinedload(Appointment.modality),
        )
        .filter(Appointment.marked_at >= lower, Appointment.marked_at < upper)
        .filter(Appointment.professional_id.in_(professional_ids))
        .order_by(Appointment.marked_at, Appointment.id)
        .all()
    )


def _payout_period(key: str, start: date, end: date, stats, payments=()) -> PayoutPeriod:
    handed = payment_totals(payments, start, end)
    return PayoutPeriod(
        period=key,
        start=start,
        end=end,
        completed=stats.completed,
        scheduled=stats.scheduled,
        cancelled=stats.cancelled,
        payout_cents=stats.payout_cents,
        advances_cents=handed.advances_cents,
        installments_cents=handed.installments_cents,
        paid_cents=handed.paid_cents,
        unconfirmed_receipts=handed.unconfirmed,
    )


def _professional_payments(db: Session, professional_ids: list[int]):
    """Payments and settlements of the professionals, grouped by professional."""
    payments: dict[int, list] = {pid: [] for pid in professional_ids}
    settlements: dict[int, list] = {pid: [] for pid in professional_ids}
    if professional_ids:
        for row in db.query(PayoutPayment).filter(PayoutPayment.payee_type == PAYEE_PROFESSIONAL, PayoutPayment.professional_id.in_(professional_ids)).all():
            payments[row.professional_id].append(row)
        for row in db.query(PayoutSettlement).filter(PayoutSettlement.payee_type == PAYEE_PROFESSIONAL, PayoutSettlement.professional_id.in_(professional_ids)).all():
            settlements[row.professional_id].append(row)
    return payments, settlements


def _payout_row(db: Session, item, start: date, end: date, payments: list, settlements: list) -> ProfessionalPayoutRow:
    professional = item.professional
    signed = db.query(Appointment).filter(Appointment.professional_id == professional.id, Appointment.status == "realizada").all()
    pos = position(signed, payments, settlements, PAYEE_PROFESSIONAL)
    handed = payment_totals(payments, start, end)
    return ProfessionalPayoutRow(
        professional_id=professional.id,
        name=professional.name,
        specialty_id=professional.specialty_id,
        specialty_name=professional.specialty.name,
        clinic_id=professional.clinic_id,
        clinic_name=professional.clinic.name,
        fee_cents=professional.price_cents,
        on_call_fee_cents=professional.on_call_price_cents if professional.on_call_enabled else 0,
        on_call=item.totals.on_call,
        on_call_cents=item.totals.on_call_cents,
        urgent_fee_cents=professional.urgent_price_cents if professional.urgent_enabled else 0,
        urgent=item.totals.urgent,
        urgent_cents=item.totals.urgent_cents,
        remote_fee_cents=remote_value(professional.remote_price_cents, professional.price_cents) if professional.remote_enabled else 0,
        remote=item.totals.remote,
        remote_cents=item.totals.remote_cents,
        completed=item.totals.completed,
        scheduled=item.totals.scheduled,
        cancelled=item.totals.cancelled,
        payout_cents=item.totals.payout_cents,
        periods=[_payout_period(bucket.key, bucket.start, bucket.end, stats, payments) for bucket, stats in item.periods],
        advances_cents=handed.advances_cents,
        installments_cents=handed.installments_cents,
        paid_cents=handed.paid_cents,
        unconfirmed_receipts=handed.unconfirmed,
        unsettled_cents=pos.unsettled_cents,
        advances_outstanding_cents=pos.advances_outstanding_cents,
        open_installments_cents=pos.open_installments_cents,
        balance_cents=pos.balance_cents,
    )


def _own_scope(user: User, name, specialty_id, clinic_id, professional_id):
    """A professional login only ever sees its own payout: the filters are
    replaced by its professional id (another id is refused)."""
    if not is_professional(user):
        return name, specialty_id, clinic_id, professional_id
    if professional_id is not None and professional_id != user.professional_id:
        raise HTTPException(status_code=403, detail="Not allowed for another professional's payout")
    return None, None, None, user.professional_id


@router.get("/payouts", response_model=PayoutsReport, dependencies=PAYOUT_VIEWERS)
def payouts_report(
    granularity: str = Query(default="month"),
    start: date | None = None,
    end: date | None = None,
    name: str | None = Query(default=None, description="Professional name (case-insensitive, partial)"),
    specialty_id: int | None = None,
    clinic_id: int | None = None,
    professional_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Amount due to each professional in the range: the professional's fee
    of every **completed** (signed) appointment, by service date. Scheduled
    and cancelled appointments are listed for control but pay nothing.
    Professionals matching the filters appear even when nothing is due.
    A professional login gets only its own row."""
    start, end = _range(granularity, start, end)
    name, specialty_id, clinic_id, professional_id = _own_scope(user, name, specialty_id, clinic_id, professional_id)
    professionals = _professionals(db, name, specialty_id, clinic_id, professional_id)
    ids = [p.id for p in professionals]
    rows = _payout_appointments(db, start, end, ids)
    payments, settlements = _professional_payments(db, ids)
    all_payments = [p for items in payments.values() for p in items]
    return PayoutsReport(
        granularity=granularity,
        start=start,
        end=end,
        professionals_count=len(professionals),
        totals=_payout_period("total", start, end, payout_stats(rows), all_payments),
        periods=[
            _payout_period(bucket.key, bucket.start, bucket.end, stats, all_payments)
            for bucket, stats in payouts_by_period(rows, start, end, granularity)
        ],
        professionals=[
            _payout_row(db, item, start, end, payments[item.professional.id], settlements[item.professional.id])
            for item in payouts_by_professional(professionals, rows, start, end, granularity)
        ],
    )


def _patient_name(row: Appointment) -> str:
    return row.dependent.name if row.dependent is not None else row.customer.name


@router.get("/payouts/{professional_id}", response_model=PayoutStatement, dependencies=PAYOUT_VIEWERS)
def payout_statement(
    professional_id: int,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Statement of one professional: every appointment in the range with the
    amount it pays (signed ones only), to back the payment. A professional
    login can only open its own statement."""
    start, end = _range("month", start, end)
    _own_scope(user, None, None, None, professional_id)
    professional = (
        db.query(Professional)
        .options(joinedload(Professional.specialty), joinedload(Professional.clinic))
        .filter(Professional.id == professional_id)
        .first()
    )
    if professional is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    rows = _payout_appointments(db, start, end, [professional.id])
    stats = payout_stats(rows)
    return PayoutStatement(
        start=start,
        end=end,
        professional_id=professional.id,
        name=professional.name,
        specialty_id=professional.specialty_id,
        specialty_name=professional.specialty.name,
        clinic_id=professional.clinic_id,
        clinic_name=professional.clinic.name,
        fee_cents=professional.price_cents,
        on_call_fee_cents=professional.on_call_price_cents if professional.on_call_enabled else 0,
        on_call=stats.on_call,
        on_call_cents=stats.on_call_cents,
        urgent_fee_cents=professional.urgent_price_cents if professional.urgent_enabled else 0,
        urgent=stats.urgent,
        urgent_cents=stats.urgent_cents,
        remote_fee_cents=remote_value(professional.remote_price_cents, professional.price_cents) if professional.remote_enabled else 0,
        remote=stats.remote,
        remote_cents=stats.remote_cents,
        completed=stats.completed,
        scheduled=stats.scheduled,
        cancelled=stats.cancelled,
        payout_cents=stats.payout_cents,
        lines=[
            PayoutStatementLine(
                appointment_id=row.id,
                marked_at=row.marked_at,
                status=row.status,
                patient=_patient_name(row),
                customer_id=row.customer_id,
                dependent_id=row.dependent_id,
                plan=row.modality.name if row.modality else None,
                over_limit=bool(row.over_limit),
                on_call=bool(row.on_call),
                urgent=bool(row.urgent),
                remote=bool(row.remote),
                gross_cents=row.gross_cents,
                discount_cents=row.discount_cents,
                amount_cents=row.amount_cents,
                payout_cents=payout_cents(row),
                signed_at=row.signed_at,
            )
            for row in rows
        ],
    )


@router.get("/payouts.csv", dependencies=PAYOUT_VIEWERS)
def payouts_csv(
    start: date | None = None,
    end: date | None = None,
    name: str | None = None,
    specialty_id: int | None = None,
    clinic_id: int | None = None,
    professional_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One CSV row per completed (signed) appointment, what backs each payout,
    with the professional and the amount due: for the finance team, or for
    the professional's own records."""
    start, end = _range("month", start, end)
    name, specialty_id, clinic_id, professional_id = _own_scope(user, name, specialty_id, clinic_id, professional_id)
    professionals = {p.id: p for p in _professionals(db, name, specialty_id, clinic_id, professional_id)}
    rows = [row for row in _payout_appointments(db, start, end, list(professionals)) if payout_cents(row)]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["date", "time", "professional_id", "professional", "specialty", "clinic", "appointment_id", "patient", "plan", "over_limit", "on_call", "urgent", "remote", "signed_at", "amount_cents", "payout_cents"]
    )
    for row in sorted(rows, key=lambda r: (professionals[r.professional_id].name.lower(), r.marked_at, r.id)):
        professional = professionals[row.professional_id]
        writer.writerow(
            [
                row.marked_at.date().isoformat(),
                row.marked_at.strftime("%H:%M"),
                professional.id,
                professional.name,
                professional.specialty.name,
                professional.clinic.name,
                row.id,
                _patient_name(row),
                row.modality.name if row.modality else "",
                "yes" if row.over_limit else "no",
                "yes" if row.on_call else "no",
                "yes" if row.urgent else "no",
                "yes" if row.remote else "no",
                row.signed_at.isoformat(timespec="minutes") if row.signed_at else "",
                row.amount_cents,
                payout_cents(row),
            ]
        )
    buffer.seek(0)
    filename = f"payouts_{start.isoformat()}_{end.isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- the employee's statement (co-payment) -------------------------------------------------

STATEMENT_READERS = [Depends(require_roles(ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_ATTENDANT, ROLE_CLIENT))]


@router.get("/employee-statement", response_model=EmployeeStatementOut, dependencies=STATEMENT_READERS)
def employee_statement_report(
    customer_id: int | None = None,
    start: str | None = Query(default=None, description="YYYY-MM; default January of this year"),
    end: str | None = Query(default=None, description="YYYY-MM; default December of this year"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A customer's own payments: its part of every fee due in the months
    (all of it for an individual plan; its share in a corporate co-payment;
    nothing when its company pays), what its company paid and what is open.
    A client login sees its own (``customer_id`` omitted or its own); the
    staff any customer's."""
    if user.role == ROLE_CLIENT:
        if customer_id is not None and customer_id != user.customer_id:
            raise HTTPException(status_code=403, detail="Not allowed for another customer")
        customer_id = user.customer_id
    if customer_id is None:
        raise HTTPException(status_code=400, detail="customer_id is required")
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    default_start, default_end = default_statement_range()
    return employee_statement(db, customer, start or default_start, end or default_end)

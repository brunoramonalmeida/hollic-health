"""Home dashboard: the main numbers of the system, tailored to the role.
Money and queue health are only included for administrators; clients get
their own appointments and plan; professionals get their own agenda, the
appointments awaiting their signature and the month's payout; sellers get
their sales, their portfolio and their commission."""

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..addresses import format_address, maps_url
from ..attendance import attendance_state, is_no_show
from ..auth import ROLE_ADMIN, STAFF_ROLES, get_current_user, is_client, is_partner, is_professional, is_seller
from ..billing import receipts
from ..billing import STATUS_ACTIVE, STATUS_OVERDUE, STATUS_PENDING, current_subscription, monthly_amount, subscription_status, dependents_count
from ..database import get_db
from ..models import AgreementCharge, Appointment, Customer, Installment, Notification, PayoutPayment, PayoutSettlement, SaleOverride, Seller, Subscription, User
from ..partners import AGREEMENT_IN_FORCE, agreement_query, agreement_status, ledgers, reference_period
from ..payouts import PAYEE_PROFESSIONAL, is_confirmed, position
from ..queue import DEAD, PENDING, stats
from ..reports import as_date, payout_cents
from ..sales import commission_cents
from ..signature import awaiting_signature
from ..schemas import DashboardAppointment, DashboardOut, DashboardSale
from .reports import received_between
from .subscriptions import _load as load_subscription, _out as subscription_out, organisation_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _appointment(row: Appointment, now: datetime) -> DashboardAppointment:
    return DashboardAppointment(
        id=row.id,
        marked_at=row.marked_at,
        patient=row.dependent.name if row.dependent else row.customer.name,
        professional=row.professional.name,
        clinic=row.clinic.name,
        clinic_address=format_address(row.clinic),
        clinic_maps_url=maps_url(row.clinic),
        remote=bool(row.remote),
        meeting_url=row.meeting_url or "",
        attendance=attendance_state(row, now),
        status=row.status,
        amount_cents=row.amount_cents,
    )


@router.get("", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = clock.today()
    now = clock.now()
    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    week_end = day_start + timedelta(days=7)
    month_start = datetime.combine(today.replace(day=1), time.min)
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = datetime.combine(next_month, time.min)

    appointments = db.query(Appointment)
    subscriptions = db.query(Subscription).options(selectinload(Subscription.installments))
    installments = db.query(Installment).join(Subscription)
    if is_client(user):
        appointments = appointments.filter(Appointment.customer_id == user.customer_id)
        subscriptions = subscriptions.filter(Subscription.customer_id == user.customer_id)
        installments = installments.filter(Subscription.customer_id == user.customer_id)
    elif is_professional(user):
        appointments = appointments.filter(Appointment.professional_id == user.professional_id)
        subscriptions = subscriptions.filter(False)
        installments = installments.filter(False)
    elif is_seller(user):
        appointments = appointments.filter(False)
        subscriptions = subscriptions.filter(Subscription.seller_id == user.seller_id)
        installments = installments.filter(Subscription.seller_id == user.seller_id)
    elif is_partner(user):
        # A company's home is its portal (GET /portal): nothing of the network here.
        appointments = appointments.filter(False)
        subscriptions = subscriptions.filter(False)
        installments = installments.filter(False)

    active = [a for a in appointments.filter(Appointment.status.in_(["agendada", "realizada"])).all()]
    month_rows = [a for a in appointments.filter(Appointment.marked_at >= month_start, Appointment.marked_at < month_end).all()]
    upcoming = (
        appointments.options(
            joinedload(Appointment.customer),
            joinedload(Appointment.dependent),
            joinedload(Appointment.professional),
            joinedload(Appointment.clinic),
        )
        .filter(Appointment.status == "agendada", Appointment.marked_at >= now)
        .order_by(Appointment.marked_at)
        .limit(8)
        .all()
    )

    counts = {STATUS_ACTIVE: 0, STATUS_OVERDUE: 0, STATUS_PENDING: 0}
    for row in subscriptions.filter(Subscription.cancelled_on.is_(None)).all():
        status = subscription_status(row, row.installments, today)
        if status in counts:
            counts[status] += 1

    due_week = installments.filter(
        Installment.paid_on.is_(None),
        Installment.due_on >= today,
        Installment.due_on < today + timedelta(days=7),
        Subscription.cancelled_on.is_(None),
    ).all()

    data = DashboardOut(
        role=user.role,
        today=today,
        appointments_today=sum(1 for a in active if day_start <= a.marked_at < day_end),
        appointments_upcoming_week=sum(1 for a in active if a.status == "agendada" and now <= a.marked_at < week_end),
        appointments_completed_month=sum(1 for a in month_rows if a.status == "realizada"),
        appointments_cancelled_month=sum(1 for a in month_rows if a.status == "cancelada"),
        next_appointments=[_appointment(row, now) for row in upcoming],
        subscriptions_active=counts[STATUS_ACTIVE],
        subscriptions_overdue=counts[STATUS_OVERDUE],
        subscriptions_pending=counts[STATUS_PENDING],
        installments_due_week=len(due_week),
        installments_due_week_cents=sum(item.amount_cents for item in due_week),
        unread_notifications=db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
        .count(),
        checked_in_today=sum(1 for a in active if a.checked_in_at is not None and day_start <= a.marked_at < day_end),
        no_shows_month=sum(1 for a in month_rows if is_no_show(a, now)),
        awaiting_signature=sum(1 for a in active if a.checked_out_at is not None and awaiting_signature(a, now)),
    )

    if user.role == ROLE_ADMIN:
        completed = [a for a in month_rows if a.status == "realizada"]
        month_last = next_month - timedelta(days=1)
        touched = db.query(Installment).filter(received_between(today.replace(day=1), month_last)).all()
        # Plan fees received this month (each part of a corporate fee on its own date); taxes once settled.
        received = sum(
            cents for item in touched for day, cents in receipts(item) if today.replace(day=1) <= day <= month_last
        )
        paid = [item for item in touched if item.paid_on is not None and today.replace(day=1) <= item.paid_on <= month_last]
        # Plan revenue: the fees paid plus the shortfalls companies paid below their minimum.
        charges = (
            db.query(AgreementCharge)
            .filter(AgreementCharge.paid_on >= today.replace(day=1), AgreementCharge.paid_on < next_month)
            .all()
        )
        plan_revenue = received + sum(item.amount_cents for item in charges)
        appt_amount = sum(a.amount_cents for a in completed)
        tax = (
            sum(a.tax_cents for a in completed)
            + sum(item.tax_cents or 0 for item in paid)
            + sum(item.tax_cents or 0 for item in charges)
        )
        data.revenue_month_cents = appt_amount + plan_revenue
        data.discounts_month_cents = sum(a.discount_cents for a in completed)
        data.taxes_month_cents = tax
        data.net_month_cents = appt_amount + plan_revenue - tax
        data.plan_revenue_month_cents = plan_revenue
        queue = stats(db)
        data.queue_pending = queue[PENDING]
        data.queue_dead = queue[DEAD]
        handed = db.query(PayoutPayment).filter(PayoutPayment.paid_on >= today.replace(day=1), PayoutPayment.paid_on < next_month).all()
        data.payouts_month_cents = sum(p.amount_cents for p in handed)
        data.receipts_unconfirmed = sum(1 for p in db.query(PayoutPayment).filter(PayoutPayment.paid_on.isnot(None)).all() if not is_confirmed(p))

    if user.role in STAFF_ROLES:
        # Corporate agreements in force, and those billing fewer employees
        # than their minimum this month (the company will pay the shortfall).
        in_force = [row for row in agreement_query(db).all() if agreement_status(row, today) == AGREEMENT_IN_FORCE]
        books = ledgers(db, [row.id for row in in_force])
        data.agreements_in_force = len(in_force)
        data.agreements_below_minimum = sum(
            1 for row in in_force if not books[row.id].meets(row.min_employees, reference_period(row, today))
        )

    if is_client(user):
        current = current_subscription(db, user.customer_id)
        if current is not None:
            data.plan = subscription_out(load_subscription(db, current.id), db, user)

    if is_seller(user):
        seller = db.query(Seller).options(joinedload(Seller.region), joinedload(Seller.leader)).filter(Seller.id == user.seller_id).first()
        sales = (
            subscriptions.options(joinedload(Subscription.customer), joinedload(Subscription.modality), joinedload(Subscription.seller))
            .order_by(Subscription.id.desc())
            .all()
        )
        data.seller_name = seller.name
        data.region_name = seller.region.name
        data.region_commission_bp = seller.region.commission_bp or 0
        data.leader_name = seller.leader.name if seller.leader is not None else None
        # The team below it, all levels: their sales pay it the override snapshotted on each.
        data.team_size = len(organisation_ids(db, seller.id)) - 1
        led_ids = db.query(SaleOverride.subscription_id).filter(SaleOverride.seller_id == seller.id)
        led = (
            db.query(Subscription)
            .options(
                joinedload(Subscription.customer),
                joinedload(Subscription.modality),
                joinedload(Subscription.seller),
                selectinload(Subscription.installments),
                selectinload(Subscription.overrides),
            )
            .filter(Subscription.id.in_(led_ids))
            .all()
        )
        rate_of = {row.id: next(snap.rate_bp for snap in row.overrides if snap.seller_id == seller.id) for row in led}
        data.team_sales_month = sum(1 for row in led if today.replace(day=1) <= as_date(row.created_at) < next_month)
        led_paid = [(row, item) for row in led for item in row.installments if item.paid_on is not None]
        data.override_total_cents = sum(commission_cents(item.paid_cents or 0, rate_of[row.id]) for row, item in led_paid)
        data.override_month_cents = sum(
            commission_cents(item.paid_cents or 0, rate_of[row.id])
            for row, item in led_paid
            if today.replace(day=1) <= item.paid_on < next_month
        )
        data.sales_total = len(sales)
        data.sales_month = sum(1 for row in sales if today.replace(day=1) <= as_date(row.created_at) < next_month)
        sold_to = {row.customer_id for row in sales}
        registered = {row.id for row in db.query(Customer.id).filter(Customer.seller_id == seller.id).all()}
        data.customers_count = len(sold_to | registered)
        paid = [(row, item) for row in sales for item in row.installments if item.paid_on is not None]
        data.commission_total_cents = sum(commission_cents(item.paid_cents or 0, row.commission_bp or 0) for row, item in paid)
        data.commission_month_cents = sum(
            commission_cents(item.paid_cents or 0, row.commission_bp or 0)
            for row, item in paid
            if today.replace(day=1) <= item.paid_on < next_month
        )
        data.recent_sales = [
            DashboardSale(
                id=row.id,
                seller_name=row.seller.name if row.seller_id != seller.id else None,
                customer_name=row.customer.name,
                modality_name=row.modality.name,
                joined_on=row.joined_on,
                status=subscription_status(row, row.installments, today),
                monthly_amount_cents=monthly_amount(row.modality, dependents_count(db, row.customer_id), row.discount_percent or 0),
                discount_percent=row.discount_percent or 0,
            )
            for row in sorted(sales + led, key=lambda row: -row.id)[:8]
        ]

    if is_professional(user):
        data.signed_month = sum(1 for a in month_rows if a.status == "realizada")
        data.payout_month_cents = sum(payout_cents(a) for a in month_rows)
        to_sign = (
            appointments.options(
                joinedload(Appointment.customer),
                joinedload(Appointment.dependent),
                joinedload(Appointment.professional),
                joinedload(Appointment.clinic),
            )
            .filter(Appointment.status == "agendada", Appointment.marked_at <= now)
            .order_by(Appointment.marked_at.desc())
            .limit(8)
            .all()
        )
        data.to_sign = [_appointment(row, now) for row in to_sign if awaiting_signature(row, now)]
        signed = [a for a in appointments.filter(Appointment.status == "realizada").all()]
        payments = db.query(PayoutPayment).filter(PayoutPayment.payee_type == PAYEE_PROFESSIONAL, PayoutPayment.professional_id == user.professional_id).all()
        settlements = db.query(PayoutSettlement).filter(PayoutSettlement.payee_type == PAYEE_PROFESSIONAL, PayoutSettlement.professional_id == user.professional_id).all()
        pos = position(signed, payments, settlements, PAYEE_PROFESSIONAL)
        data.balance_cents = pos.balance_cents
        data.receipts_to_sign = pos.receipts_to_confirm
    return data

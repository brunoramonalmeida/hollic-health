from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..addresses import format_address, maps_url
from ..attendance import (
    CHECKIN_OPENS_BEFORE,
    AttendanceError,
    assert_can_check_in,
    assert_can_check_out,
    attendance_state,
    can_check_in,
    can_check_out,
    checkin_window,
)
from ..auth import (
    ensure_appointment_access,
    ensure_customer_access,
    get_current_user,
    is_client,
    is_professional,
    require_appointment_area,
    require_staff,
)
from ..billing import CoverageError, check_customer_coverage, current_subscription
from ..capacity import CapacityError, check_capacity
from ..database import get_db
from ..models import Appointment, Customer, Dependent, Professional, User
from ..notifications import (
    appointment_changed,
    appointment_checked_in,
    appointment_checked_out,
    appointment_created,
    appointment_signed,
    appointment_started,
    card_locked,
)
from ..chat import delete_conversation
from ..prescriptions import purge_appointment as purge_prescriptions
from ..pricing import price_appointment, price_components, resolve_discount
from ..remote import apply_meeting_link
from ..scheduling import SlotError, check_slot, normalize_marked_at, occupies_slot, on_call_slot
from ..schemas import APPOINTMENT_STATUSES, AppointmentIn, AppointmentOut, CheckInBody, QuoteOut, RevokeBody, SignBody, StartBody
from ..signing import SigningError, validate_signature_image, verify_credentials
from ..signature import (
    SignatureError,
    assert_can_revoke,
    assert_can_sign,
    awaiting_signature,
    can_revoke,
    can_sign,
    check_signed_edit,
    check_status_change,
    clear_signature,
    is_signed,
    revoke,
    sign,
)
from ..taxation import active_rules, apply_appointment_taxes, line_out
from ..taxes import KIND_APPOINTMENT, ServiceContext, compute_taxes, total
from ..usage import UNLIMITED, plan_usage
from ..virtual_card import CardError, assert_can_start, can_start, register_failure, start, undo_start, verify_code
from .customers import ensure_card_secret

router = APIRouter(
    prefix="/appointments", tags=["appointments"], dependencies=[Depends(require_appointment_area)]
)


def _out(row: Appointment, user: User | None = None, now: datetime | None = None) -> AppointmentOut:
    now = now or clock.now()
    opens, closes = checkin_window(row.marked_at)
    return AppointmentOut(
        id=row.id,
        customer_id=row.customer_id,
        customer_name=row.customer.name,
        dependent_id=row.dependent_id,
        dependent_name=row.dependent.name if row.dependent else None,
        professional_id=row.professional_id,
        professional_name=row.professional.name,
        specialty_id=row.specialty_id,
        specialty_name=row.specialty.name,
        clinic_id=row.clinic_id,
        clinic_name=row.clinic.name,
        marked_at=row.marked_at,
        status=row.status,
        on_call=bool(row.on_call),
        urgent=bool(row.urgent),
        remote=bool(row.remote),
        meeting_url=row.meeting_url or "",
        fee_cents=row.fee_cents,
        specialty_price_cents=row.specialty_price_cents,
        professional_price_cents=row.professional_price_cents,
        gross_cents=row.gross_cents,
        modality_id=row.modality_id,
        modality_name=row.modality.name if row.modality else None,
        discount_percent=row.discount_percent,
        discount_cents=row.discount_cents,
        amount_cents=row.amount_cents,
        over_limit=bool(row.over_limit),
        plan_limit=row.plan_limit or 0,
        plan_limit_period=row.plan_limit_period or "",
        tax_cents=row.tax_cents,
        net_cents=row.net_cents,
        taxes=[line_out(line) for line in row.taxes],
        notes=row.notes or "",
        created_by_id=row.created_by_id,
        created_by_name=row.created_by.name if row.created_by else None,
        created_at=row.created_at,
        clinic_address=format_address(row.clinic),
        clinic_phone=row.clinic.phone or "",
        clinic_directions=row.clinic.directions or "",
        clinic_latitude=row.clinic.latitude,
        clinic_longitude=row.clinic.longitude,
        clinic_maps_url=maps_url(row.clinic),
        attendance=attendance_state(row, now),
        checked_in_at=row.checked_in_at,
        checked_in_by_name=row.checked_in_by.name if row.checked_in_by else None,
        checked_out_at=row.checked_out_at,
        checked_out_by_name=row.checked_out_by.name if row.checked_out_by else None,
        can_check_in=can_check_in(row, now),
        can_check_out=can_check_out(row, now),
        check_in_opens_at=opens,
        check_in_closes_at=closes,
        started_at=row.started_at,
        started_by_name=row.started_by.name if row.started_by else None,
        can_start=user is not None and _may_start(user, row) and can_start(row, now, CHECKIN_OPENS_BEFORE),
        start_locked_until=row.start_locked_until,
        signed_at=row.signed_at,
        signed_by_name=row.signed_by.name if row.signed_by else None,
        signature_image=row.signature_image,
        awaiting_signature=awaiting_signature(row, now),
        can_sign=user is not None and can_sign(row, user, now),
        can_revoke_signature=user is not None and can_revoke(row, user),
        prescriptions_count=len(row.documents),
    )


# Fields of the body that are not columns of the appointment as they come.
BODY_ONLY = {"meeting_url"}


def _may_start(user: User, row: Appointment) -> bool:
    """Who types the patient's code: the staff (reception) or the appointment's professional."""
    if is_client(user):
        return False
    if is_professional(user):
        return user.professional_id == row.professional_id
    return True


def _query(db: Session):
    return db.query(Appointment).options(
        joinedload(Appointment.customer),
        joinedload(Appointment.dependent),
        joinedload(Appointment.professional),
        joinedload(Appointment.specialty),
        joinedload(Appointment.clinic),
        joinedload(Appointment.modality),
        joinedload(Appointment.created_by),
        joinedload(Appointment.checked_in_by),
        joinedload(Appointment.checked_out_by),
        joinedload(Appointment.started_by),
        joinedload(Appointment.signed_by),
        selectinload(Appointment.taxes),
        selectinload(Appointment.documents),
    )


def _load_professional(db: Session, professional_id: int) -> Professional:
    professional = (
        db.query(Professional)
        .options(joinedload(Professional.specialty), joinedload(Professional.clinic))
        .filter(Professional.id == professional_id)
        .first()
    )
    if professional is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    return professional


def _validate(db: Session, body: AppointmentIn, user: User, row: Appointment | None = None) -> Professional:
    if is_professional(user):
        raise HTTPException(status_code=403, detail="Professionals do not book or edit appointments; sign them instead")
    ensure_customer_access(user, body.customer_id)
    if body.status not in APPOINTMENT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid appointment status")
    if is_client(user) and body.meeting_url:
        raise HTTPException(status_code=403, detail="Only the staff sets the meeting link; the system issues one for you")
    try:
        check_status_change(row, body.status)
        if row is not None:
            check_signed_edit(row, body)
    except SignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if db.get(Customer, body.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.dependent_id is not None:
        dependent = db.get(Dependent, body.dependent_id)
        if dependent is None or dependent.customer_id != body.customer_id:
            raise HTTPException(status_code=404, detail="Dependent not found")
    return _load_professional(db, body.professional_id)


def _needs_coverage_check(row: Appointment | None, body: AppointmentIn) -> bool:
    """The plan is checked when an appointment is booked: on create, and on
    edit when it moves to another patient or day, or is re-activated. Editing
    notes or marking a past appointment as completed does not re-check, so a
    plan that lapsed later does not block record keeping. The same events
    re-evaluate the plan's appointment allowance (see ``_apply_plan``)."""
    if not occupies_slot(body.status):
        return False
    if row is None or not occupies_slot(row.status):
        return True
    return (
        row.customer_id != body.customer_id
        or row.dependent_id != body.dependent_id
        or row.marked_at.date() != body.marked_at.date()
    )


def _check_coverage(db: Session, body: AppointmentIn):
    try:
        check_customer_coverage(db, body.customer_id, body.marked_at.date())
    except CoverageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _check_slot(
    db: Session, body: AppointmentIn, professional: Professional, exclude_id: int | None = None
):
    try:
        check_slot(
            db,
            professional=professional,
            customer_id=body.customer_id,
            dependent_id=body.dependent_id,
            marked_at=body.marked_at,
            status=body.status,
            exclude_id=exclude_id,
            urgent=body.urgent,
            remote=bool(body.remote),
        )
    except SlotError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _needs_capacity_check(row: Appointment | None, body: AppointmentIn, professional: Professional) -> bool:
    """Capacity is taken when an appointment is booked: on create, and on edit
    when it moves to another professional (another clinic goes with it) or
    day, or is re-activated. An appointment already booked keeps its place:
    editing notes, changing the time within the day or signing never
    re-checks, so a limit lowered below what is booked does not block record
    keeping (only new bookings)."""
    if not occupies_slot(body.status):
        return False  # cancelling is always allowed and frees the place
    if row is None or not occupies_slot(row.status):
        return True
    return row.professional_id != professional.id or row.marked_at.date() != body.marked_at.date()


def _check_capacity(db: Session, body: AppointmentIn, professional: Professional, exclude_id: int | None = None):
    try:
        check_capacity(db, professional, professional.clinic, body.marked_at.date(), exclude_id)
    except CapacityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _plan_for(db: Session, customer_id: int, specialty_id: int, on: date, exclude_id: int | None = None):
    """The customer's current plan, the discount it grants for the specialty
    and the usage of its appointment allowance in the period of ``on``.
    Once the allowance is used up the discount is zero: full price."""
    subscription = current_subscription(db, customer_id)
    if subscription is None:
        return None, 0, UNLIMITED
    modality = subscription.modality
    usage = plan_usage(db, subscription, on, exclude_id)
    percent = resolve_discount(modality, modality.discounts, specialty_id) if usage.covered else 0
    return modality, percent, usage


def _quote(
    db: Session,
    customer_id: int,
    professional: Professional,
    on: date,
    exclude_id: int | None = None,
    on_call: bool = False,
    urgent: bool = False,
    remote: bool = False,
):
    """Price list (regular, on-call for an on-call slot, the urgent rate
    for an urgent visit, or the remote one for a remote appointment) + the
    discount of the customer's current plan for the specialty, unless the
    plan's appointment limit is already used up."""
    modality, percent, usage = _plan_for(db, customer_id, professional.specialty_id, on, exclude_id)
    fee, specialty_price, professional_price = price_components(
        professional.clinic, professional.specialty, professional, on_call, urgent, remote
    )
    price = price_appointment(fee, specialty_price, professional_price, percent)
    return price, modality, usage


def _apply_plan(row: Appointment, db: Session):
    """Snapshot the plan side of the price: the discount for the specialty,
    or none when the plan's allowance for the period is used up. The price
    list snapshot (fees) is kept; the taxes follow the new amount."""
    modality, percent, usage = _plan_for(db, row.customer_id, row.specialty_id, row.marked_at.date(), row.id)
    price = price_appointment(row.fee_cents, row.specialty_price_cents, row.professional_price_cents, percent)
    row.modality_id = modality.id if modality is not None else None
    row.discount_percent = price.discount_percent
    row.discount_cents = price.discount_cents
    row.amount_cents = price.amount_cents
    row.over_limit = modality is not None and not usage.covered
    row.plan_limit = modality.appointment_limit if modality is not None else 0
    row.plan_limit_period = (modality.appointment_limit_period or "") if modality is not None else ""
    apply_appointment_taxes(db, row)


def _apply_prices(row: Appointment, db: Session, professional: Professional):
    """Snapshot the price list (the on-call one when the slot is an on-call
    shift, the remote one for a remote appointment) and the plan discount at
    scheduling time. Later changes to prices or discounts do not alter this
    record."""
    row.specialty_id = professional.specialty_id
    row.clinic_id = professional.clinic_id
    row.urgent = bool(row.urgent)
    row.remote = bool(row.remote)
    row.on_call = False if (row.urgent or row.remote) else on_call_slot(db, professional, row.marked_at)
    row.fee_cents, row.specialty_price_cents, row.professional_price_cents = price_components(
        professional.clinic, professional.specialty, professional, row.on_call, row.urgent, row.remote
    )
    row.gross_cents = row.fee_cents + row.specialty_price_cents + row.professional_price_cents
    row.clinic = professional.clinic
    _apply_plan(row, db)


@router.get("", response_model=list[AppointmentOut])
def list_appointments(
    customer_id: int | None = Query(default=None),
    start: date | None = Query(default=None, description="First day, inclusive (e.g. the week or month shown by an agenda)"),
    end: date | None = Query(default=None, description="Last day, inclusive"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The appointments the user may see (scoped to its own for clients and
    professionals), optionally inside a range of days."""
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=400, detail="end must not be before start")
    query = _query(db)
    if is_client(user):
        query = query.filter(Appointment.customer_id == user.customer_id)
    elif is_professional(user):
        query = query.filter(Appointment.professional_id == user.professional_id)
    elif customer_id is not None:
        query = query.filter(Appointment.customer_id == customer_id)
    if start is not None:
        query = query.filter(Appointment.marked_at >= datetime.combine(start, datetime.min.time()))
    if end is not None:
        query = query.filter(Appointment.marked_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    rows = query.order_by(Appointment.marked_at.desc()).all()
    now = clock.now()
    return [_out(row, user, now) for row in rows]


@router.get("/quote", response_model=QuoteOut)
def quote(
    customer_id: int,
    professional_id: int,
    on: date | None = Query(default=None, description="Service date: plan allowance period and tax rules (default: today)"),
    at: datetime | None = Query(default=None, description="The slot (date and time): decides the date and whether it is an on-call service"),
    urgent: bool = Query(default=False, description="Quote an ad-hoc urgent visit (the professional must offer them; never inside an on-call shift)"),
    remote: bool = Query(default=False, description="Quote a remote appointment (the professional must offer them; a regular slot, never inside an on-call shift, never urgent)"),
    appointment_id: int | None = Query(default=None, description="When editing: the appointment itself does not count against the allowance"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What the appointment would cost now for this customer and professional
    on the given date — with the on-call price list when ``at`` falls in an
    on-call shift — including whether the plan's appointment allowance for
    that period is already used up (then full price)."""
    ensure_customer_access(user, customer_id)
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    professional = _load_professional(db, professional_id)
    on_call = False
    if at is not None:
        at = normalize_marked_at(at)
        on = at.date()
        on_call = on_call_slot(db, professional, at)
    if urgent:
        if not professional.urgent_enabled:
            raise HTTPException(status_code=400, detail="Professional does not take urgent visits")
        if on_call:
            raise HTTPException(status_code=409, detail="Professional is on call at that time: urgent visits are not accepted during on-call shifts")
    if remote:
        if not professional.remote_enabled:
            raise HTTPException(status_code=400, detail="Professional does not offer remote appointments")
        if urgent:
            raise HTTPException(status_code=400, detail="Urgent visits are attended in person: a remote appointment cannot be urgent")
        if on_call:
            raise HTTPException(status_code=409, detail="Professional is on call at that time: remote appointments are not accepted during on-call shifts")
    on = on or clock.today()
    price, modality, usage = _quote(db, customer_id, professional, on, appointment_id, on_call, urgent, remote)
    ctx = ServiceContext(
        kind=KIND_APPOINTMENT,
        on=on,
        state=professional.clinic.state or "",
        city=professional.clinic.city or "",
        ibge_code=professional.clinic.ibge_code or "",
        clinic_id=professional.clinic_id,
        specialty_id=professional.specialty_id,
        modality_id=modality.id if modality is not None else None,
    )
    lines = compute_taxes(active_rules(db), ctx, amount_cents=price.amount_cents, gross_cents=price.gross_cents)
    return QuoteOut(
        **price.__dict__,
        on_call=on_call,
        urgent=urgent,
        remote=remote,
        modality_id=modality.id if modality is not None else None,
        modality_name=modality.name if modality is not None else None,
        tax_cents=total(lines),
        net_cents=price.amount_cents - total(lines),
        taxes=[line_out(line) for line in lines],
        over_limit=modality is not None and not usage.covered,
        plan_limit=usage.limit if modality is not None else 0,
        plan_limit_period=usage.period if modality is not None else "",
        plan_used=usage.used,
        plan_remaining=usage.remaining if modality is not None else None,
        plan_window_start=usage.window_start,
        plan_window_end=usage.window_end,
    )


@router.post("", response_model=AppointmentOut, status_code=201)
def create_appointment(
    body: AppointmentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    body.urgent = bool(body.urgent)
    body.remote = bool(body.remote)
    professional = _validate(db, body, user)
    if _needs_coverage_check(None, body):
        _check_coverage(db, body)
    _check_slot(db, body, professional)
    if _needs_capacity_check(None, body, professional):
        _check_capacity(db, body, professional)
    row = Appointment(**body.model_dump(exclude=BODY_ONLY), created_by_id=user.id)
    _apply_prices(row, db, professional)
    apply_meeting_link(row, row.remote, body.meeting_url)
    db.add(row)
    db.flush()
    saved = _query(db).filter(Appointment.id == row.id).one()
    appointment_created(db, saved, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == row.id).one(), user)


@router.get("/{item_id}", response_model=AppointmentOut)
def get_appointment(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _query(db).filter(Appointment.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    ensure_appointment_access(user, row)
    return _out(row, user)


@router.put("/{item_id}", response_model=AppointmentOut)
def update_appointment(
    item_id: int, body: AppointmentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    row = db.get(Appointment, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if is_professional(user):
        raise HTTPException(status_code=403, detail="Professionals do not book or edit appointments; sign them instead")
    ensure_customer_access(user, row.customer_id)
    if body.urgent is None:
        body.urgent = bool(row.urgent)  # omitted: the kind of visit stays as it is
    if body.remote is None:
        body.remote = bool(row.remote)  # omitted: in person / remote stays as it is
    professional = _validate(db, body, user, row)
    rebooked = _needs_coverage_check(row, body)
    if rebooked:
        _check_coverage(db, body)
    _check_slot(db, body, professional, exclude_id=item_id)
    if _needs_capacity_check(row, body, professional):
        _check_capacity(db, body, professional, exclude_id=item_id)
    # Another professional (specialty/clinic/fee), another plan, or another
    # kind of service (regular <-> on-call slot, urgent or not, remote or in
    # person): the price list changes.
    reprice = (
        row.professional_id != professional.id
        or row.customer_id != body.customer_id
        or bool(row.urgent) != body.urgent
        or bool(row.remote) != body.remote
        or (not body.urgent and not body.remote and bool(row.on_call) != on_call_slot(db, professional, body.marked_at))
    )
    previous = {"status": row.status, "marked_at": row.marked_at, "professional_id": row.professional_id, "remote": bool(row.remote)}
    for key, value in body.model_dump(exclude=BODY_ONLY).items():
        setattr(row, key, value)
    apply_meeting_link(row, row.remote, body.meeting_url)
    if row.status == "cancelada" and is_signed(row):
        clear_signature(row)  # the staff cancelled a signed appointment
        row.signature_image = None
    if row.status == "cancelada" and previous["status"] != "cancelada":
        purge_prescriptions(db, row)  # a cancelled visit keeps no prescriptions
    if reprice:
        _apply_prices(row, db, professional)
    elif rebooked:
        # Same price list, but another day or a re-activation: the plan's
        # allowance for that period may differ (and so may the tax rules).
        _apply_plan(row, db)
    if previous["marked_at"] != row.marked_at:
        # New slot: remind again for the new time.
        row.reminded_day_at = None
        row.reminded_hour_at = None
        if not reprice and not rebooked and previous["marked_at"].date() != row.marked_at.date():
            # Same price, another service date: the tax rules in force may differ.
            apply_appointment_taxes(db, row)
    db.flush()
    db.expire(row)
    saved = _query(db).filter(Appointment.id == item_id).one()
    appointment_changed(db, saved, previous, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


def _load_for_attendance(db: Session, item_id: int, user: User) -> Appointment:
    row = _query(db).filter(Appointment.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    ensure_appointment_access(user, row)
    return row


@router.post("/{item_id}/check-in", response_model=AppointmentOut)
def check_in(
    item_id: int,
    body: CheckInBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confirm arrival. Allowed from 2 h before to 2 h after the slot (see app.attendance)."""
    row = _load_for_attendance(db, item_id, user)
    now = clock.now()
    try:
        assert_can_check_in(row, now)
    except AttendanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    row.checked_in_at = now.replace(microsecond=0)
    row.checked_in_by_id = user.id
    if body is not None:
        row.checked_in_lat, row.checked_in_lng = body.latitude, body.longitude
        if body.notes:
            row.notes = f"{row.notes}\n[check-in] {body.notes}".strip()
    db.flush()
    appointment_checked_in(db, row, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.delete("/{item_id}/check-in", response_model=AppointmentOut, dependencies=[Depends(require_staff)])
def undo_check_in(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reception correction: remove a check-in registered by mistake (before check-out)."""
    row = _load_for_attendance(db, item_id, user)
    if row.checked_in_at is None:
        raise HTTPException(status_code=409, detail="Appointment has no check-in")
    if row.checked_out_at is not None or row.status == "realizada":
        raise HTTPException(status_code=409, detail="Cannot undo the check-in of a completed appointment")
    if row.started_at is not None:
        raise HTTPException(status_code=409, detail="Cannot undo the check-in of a started appointment; undo the start first")
    row.checked_in_at = None
    row.checked_in_by_id = None
    row.checked_in_lat = row.checked_in_lng = None
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.post("/{item_id}/check-out", response_model=AppointmentOut)
def check_out(
    item_id: int,
    body: CheckInBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confirm the end of the visit. The appointment is then *awaiting the
    professional's signature*, which is what completes it."""
    row = _load_for_attendance(db, item_id, user)
    now = clock.now()
    try:
        assert_can_check_out(row, now)
    except AttendanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    row.checked_out_at = now.replace(microsecond=0)
    row.checked_out_by_id = user.id
    if body is not None:
        row.checked_out_lat, row.checked_out_lng = body.latitude, body.longitude
        if body.notes:
            row.notes = f"{row.notes}\n[check-out] {body.notes}".strip()
    db.flush()
    appointment_checked_out(db, row, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.post("/{item_id}/start", response_model=AppointmentOut)
def start_appointment(
    item_id: int,
    body: StartBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start the visit by typing the code of the patient's virtual card.

    The reception or the appointment's professional types the code the
    patient shows; the current and the previous code (30 s each) are
    accepted. Wrong codes are counted and lock the appointment for a while
    (``429``). Starting registers the arrival when there was no check-in, and
    is required before the professional can sign."""
    row = _load_for_attendance(db, item_id, user)
    if not _may_start(user, row):
        raise HTTPException(status_code=403, detail="Only the staff or the appointment's professional can start it")
    now = clock.now()
    try:
        assert_can_start(row, now, CHECKIN_OPENS_BEFORE)
    except CardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    patient = row.dependent if row.dependent is not None else row.customer
    secret = ensure_card_secret(db, patient)
    if not verify_code(secret, body.code, now):
        locked = register_failure(row, now)
        if locked:
            card_locked(db, row, user)
        db.commit()
        if locked:
            raise HTTPException(status_code=429, detail=f"Too many wrong codes; locked until {row.start_locked_until:%H:%M}")
        raise HTTPException(status_code=400, detail="Wrong card code; ask the patient for the code shown now")
    start(row, user, now)
    db.flush()
    appointment_started(db, row, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.delete("/{item_id}/start", response_model=AppointmentOut, dependencies=[Depends(require_staff)])
def undo_start_appointment(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reception correction: undo a start registered on the wrong appointment (before the signature)."""
    row = _load_for_attendance(db, item_id, user)
    if row.started_at is None:
        raise HTTPException(status_code=409, detail="Appointment was not started")
    if row.signed_at is not None or row.status == "realizada":
        raise HTTPException(status_code=409, detail="Cannot undo the start of a completed appointment")
    undo_start(row)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


def _strong_signature(db: Session, user: User, password: str, code: str, now: datetime) -> None:
    """Password + authenticator code; failures are counted on the user even when refused."""
    try:
        verify_credentials(user, password, code, now)
    except SigningError as exc:
        db.commit()  # persist the failure count / lock
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{item_id}/sign", response_model=AppointmentOut)
def sign_appointment(
    item_id: int,
    body: SignBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The professional attests the visit: the appointment becomes completed
    and enters the payout. Only the professional's own login can sign, after
    the slot started and the patient's card was verified (see app.signature),
    with its password, its authenticator code and a hand-drawn signature
    (see app.signing)."""
    row = _load_for_attendance(db, item_id, user)
    now = clock.now()
    previous = {"status": row.status, "marked_at": row.marked_at, "professional_id": row.professional_id}
    try:
        assert_can_sign(row, user, now)
        image = validate_signature_image(body.signature_image)
    except SignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except SigningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _strong_signature(db, user, body.password, body.code, now)
    sign(row, user, now)
    row.signature_image = body.signature_image
    if body.notes:
        row.notes = f"{row.notes}\n[signature] {body.notes}".strip()
    db.flush()
    appointment_signed(db, row, previous, user)
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.delete("/{item_id}/sign", response_model=AppointmentOut)
def revoke_signature(item_id: int, body: RevokeBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The professional withdraws a signature given by mistake (password and
    authenticator code again): the appointment returns to scheduled and
    leaves the payout."""
    row = _load_for_attendance(db, item_id, user)
    try:
        assert_can_revoke(row, user)
    except SignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _strong_signature(db, user, body.password, body.code, clock.now())
    revoke(row, user)
    row.signature_image = None
    db.commit()
    return _out(_query(db).filter(Appointment.id == item_id).one(), user)


@router.delete("/{item_id}", status_code=204, dependencies=[Depends(require_staff)])
def delete_appointment(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Appointment, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    purge_prescriptions(db, row)  # the files go with the record
    delete_conversation(db, row)  # and so does the chat
    db.delete(row)
    db.commit()

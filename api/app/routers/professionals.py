from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ..auth import get_current_user, is_professional, require_network, require_pricing, require_staff
from ..database import get_db
from ..models import Appointment, Clinic, Professional, Specialty, TimeOff, User, WorkingHours
from ..capacity import SLOT_FULL, full_days
from ..pricing import remote_value, urgent_minimum
from ..scheduling import DEFAULT_SLOT_MINUTES, DEFAULT_WEEK, SLOT_AVAILABLE, build_slots
from ..schemas import (
    DaySlotsOut,
    DefaultWeekOut,
    ProfessionalIn,
    ProfessionalOut,
    RemoteAvailabilityIn,
    SlotOut,
    TimeOffIn,
    TimeOffOut,
    UrgentAvailabilityIn,
    WorkingHoursIn,
    WorkingHoursOut,
    WorkingHoursPut,
)

router = APIRouter(
    prefix="/professionals", tags=["professionals"], dependencies=[Depends(require_network)]
)

MAX_SLOT_RANGE_DAYS = 62

# Fees and working hours move prices/slots: admins and supervisors only.
# Time off is day-to-day agenda management: any staff member.
WRITE = [Depends(require_pricing)]
STAFF = [Depends(require_staff)]


def _hours_out(rows, on_call: bool = False) -> list[WorkingHoursOut]:
    """The professional's ranges of one kind: regular hours or on-call shifts."""
    return [
        WorkingHoursOut(weekday=row.weekday, start=row.start_time, end=row.end_time)
        for row in sorted(rows, key=lambda item: (item.weekday, item.start_time))
        if bool(row.on_call) == on_call
    ]


def _out(row: Professional) -> ProfessionalOut:
    return ProfessionalOut(
        id=row.id,
        name=row.name,
        document=row.document or "",
        specialty_id=row.specialty_id,
        specialty_name=row.specialty.name,
        specialty_price_cents=row.specialty.price_cents,
        clinic_id=row.clinic_id,
        clinic_name=row.clinic.name,
        clinic_fee_cents=row.clinic.fee_cents,
        price_cents=row.price_cents,
        slot_minutes=row.slot_minutes,
        on_call_enabled=bool(row.on_call_enabled),
        on_call_price_cents=row.on_call_price_cents or 0,
        on_call_hours=_hours_out(row.working_hours, on_call=True),
        clinic_on_call_fee_cents=row.clinic.on_call_fee_cents,
        specialty_on_call_price_cents=row.specialty.on_call_price_cents,
        urgent_enabled=bool(row.urgent_enabled),
        urgent_price_cents=row.urgent_price_cents or 0,
        urgent_minimum_cents=urgent_minimum(row.price_cents),
        remote_enabled=bool(row.remote_enabled),
        remote_price_cents=row.remote_price_cents,
        remote_fee_cents=remote_value(row.remote_price_cents, row.price_cents),
        clinic_remote_fee_cents=row.clinic.remote_fee_cents,
        specialty_remote_price_cents=row.specialty.remote_price_cents,
        daily_limit=row.daily_limit or 0,
        monthly_limit=row.monthly_limit or 0,
        clinic_daily_limit=row.clinic.daily_limit or 0,
        clinic_monthly_limit=row.clinic.monthly_limit or 0,
        working_hours=_hours_out(row.working_hours),
    )


def _time_off_out(row: TimeOff) -> TimeOffOut:
    return TimeOffOut(
        id=row.id,
        professional_id=row.professional_id,
        date=row.date,
        start=row.start_time,
        end=row.end_time,
        reason=row.reason or "",
    )


def _default_ranges() -> list[WorkingHoursIn]:
    return [WorkingHoursIn(weekday=w, start=s, end=e) for w, s, e in DEFAULT_WEEK]


def _query(db: Session):
    return db.query(Professional).options(
        joinedload(Professional.specialty),
        joinedload(Professional.clinic),
        selectinload(Professional.working_hours),
    )


def _load(db: Session, item_id: int) -> Professional:
    row = _query(db).filter(Professional.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    return row


def _require_links(db: Session, body: ProfessionalIn):
    if db.get(Specialty, body.specialty_id) is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    if db.get(Clinic, body.clinic_id) is None:
        raise HTTPException(status_code=404, detail="Clinic not found")


def _check_overlaps(ranges: list[WorkingHoursIn], label: str = "Working hours"):
    by_day: dict[int, list[WorkingHoursIn]] = {}
    for item in ranges:
        by_day.setdefault(item.weekday, []).append(item)
    for weekday, items in by_day.items():
        items.sort(key=lambda item: item.start)
        for previous, current in zip(items, items[1:]):
            if current.start < previous.end:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} overlap on weekday {weekday}: "
                    f"{previous.start}-{previous.end} and {current.start}-{current.end}",
                )


def _check_kinds_apart(regular: list, on_call: list):
    """A slot is either regular or on-call: the two kinds never overlap on a
    weekday (the price list of the slot must be unambiguous)."""
    for span in on_call:
        for other in regular:
            if span.weekday == other.weekday and span.start < other.end and other.start < span.end:
                raise HTTPException(
                    status_code=400,
                    detail=f"On-call shift {span.start}-{span.end} overlaps the working hours "
                    f"{other.start}-{other.end} on weekday {span.weekday}",
                )


def _as_ranges(rows, on_call: bool) -> list[WorkingHoursIn]:
    return [WorkingHoursIn(weekday=r.weekday, start=r.start_time, end=r.end_time) for r in rows if bool(r.on_call) == on_call]


def _replace_hours(db: Session, professional_id: int, ranges: list[WorkingHoursIn], on_call: bool = False):
    """Replace the professional's ranges of one kind (regular or on-call)."""
    _check_overlaps(ranges, "On-call shifts" if on_call else "Working hours")
    db.query(WorkingHours).filter(WorkingHours.professional_id == professional_id, WorkingHours.on_call == on_call).delete()
    for item in ranges:
        db.add(
            WorkingHours(
                professional_id=professional_id,
                weekday=item.weekday,
                start_time=item.start,
                end_time=item.end,
                on_call=on_call,
            )
        )


@router.get("/default-week", response_model=DefaultWeekOut)
def default_week():
    """The week applied to a professional created without explicit hours."""
    return DefaultWeekOut(
        slot_minutes=DEFAULT_SLOT_MINUTES,
        working_hours=[WorkingHoursOut(weekday=w, start=s, end=e) for w, s, e in DEFAULT_WEEK],
    )


@router.get("", response_model=list[ProfessionalOut])
def list_professionals(db: Session = Depends(get_db)):
    rows = _query(db).order_by(Professional.name).all()
    return [_out(row) for row in rows]


HOURS_FIELDS = {"working_hours", "on_call_hours"}


@router.post("", response_model=ProfessionalOut, status_code=201, dependencies=WRITE)
def create_professional(body: ProfessionalIn, db: Session = Depends(get_db)):
    _require_links(db, body)
    data = body.model_dump(exclude=HOURS_FIELDS)
    row = Professional(**data)
    db.add(row)
    db.flush()
    ranges = body.working_hours if body.working_hours is not None else _default_ranges()
    on_call = body.on_call_hours or []
    _check_kinds_apart(ranges, on_call)
    _replace_hours(db, row.id, ranges)
    _replace_hours(db, row.id, on_call, on_call=True)
    db.commit()
    return _out(_load(db, row.id))


# --- the professional's own profile ----------------------------------------------


def _own(db: Session, user: User) -> Professional:
    if not is_professional(user) or user.professional_id is None:
        raise HTTPException(status_code=403, detail="Only a professional login has a professional profile")
    return _load(db, user.professional_id)


@router.get("/me", response_model=ProfessionalOut)
def my_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The professional record behind the current login."""
    return _out(_own(db, user))


@router.put("/me/urgent", response_model=ProfessionalOut)
def set_my_urgent_availability(body: UrgentAvailabilityIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The professional flags whether it takes ad-hoc urgent visits and at
    which rate (at least three times its regular fee). Urgent visits are
    never accepted during its on-call shifts."""
    row = _own(db, user)
    minimum = urgent_minimum(row.price_cents)
    if body.urgent_enabled:
        rate = body.urgent_price_cents if body.urgent_price_cents is not None else max(row.urgent_price_cents or 0, minimum)
        if rate < minimum:
            raise HTTPException(status_code=400, detail=f"The urgent rate must be at least three times your fee ({minimum} cents)")
        row.urgent_price_cents = rate
    elif body.urgent_price_cents is not None:
        row.urgent_price_cents = body.urgent_price_cents
    row.urgent_enabled = body.urgent_enabled
    db.commit()
    db.expire_all()
    return _out(_load(db, row.id))


@router.put("/me/remote", response_model=ProfessionalOut)
def set_my_remote_availability(body: RemoteAvailabilityIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The professional flags whether it offers remote appointments (video
    call) and at which rate: ``remote_price_cents`` sets it, omitted keeps
    the current one, ``same_as_fee`` drops it (remote appointments then pay
    the regular fee). Remote appointments are booked in the regular working
    hours, never in the on-call shifts."""
    row = _own(db, user)
    if body.same_as_fee:
        row.remote_price_cents = None
    elif body.remote_price_cents is not None:
        row.remote_price_cents = body.remote_price_cents
    row.remote_enabled = body.remote_enabled
    db.commit()
    db.expire_all()
    return _out(_load(db, row.id))


@router.get("/{item_id}", response_model=ProfessionalOut)
def get_professional(item_id: int, db: Session = Depends(get_db)):
    return _out(_load(db, item_id))


@router.put("/{item_id}", response_model=ProfessionalOut, dependencies=WRITE)
def update_professional(item_id: int, body: ProfessionalIn, db: Session = Depends(get_db)):
    row = db.get(Professional, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    _require_links(db, body)
    for key, value in body.model_dump(exclude=HOURS_FIELDS).items():
        setattr(row, key, value)
    current = db.query(WorkingHours).filter(WorkingHours.professional_id == item_id).all()
    regular = body.working_hours if body.working_hours is not None else _as_ranges(current, on_call=False)
    if not body.on_call_enabled:
        on_call: list[WorkingHoursIn] = []  # no longer available: the shifts go with it
    elif body.on_call_hours is not None:
        on_call = body.on_call_hours
    else:
        on_call = _as_ranges(current, on_call=True)
    _check_kinds_apart(regular, on_call)
    if body.working_hours is not None:
        _replace_hours(db, item_id, body.working_hours)
    if body.on_call_hours is not None or not body.on_call_enabled:
        _replace_hours(db, item_id, on_call, on_call=True)
    db.commit()
    db.expire_all()
    return _out(_load(db, item_id))


@router.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_professional(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Professional, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


# --- working hours --------------------------------------------------------------


@router.get("/{item_id}/working-hours", response_model=list[WorkingHoursOut])
def get_working_hours(item_id: int, db: Session = Depends(get_db)):
    return _hours_out(_load(db, item_id).working_hours)


@router.put("/{item_id}/working-hours", response_model=list[WorkingHoursOut], dependencies=WRITE)
def put_working_hours(item_id: int, body: WorkingHoursPut, db: Session = Depends(get_db)):
    """Replace the regular hours (and the on-call shifts when ``on_call_ranges``
    is given; they need the professional to be available for on-call)."""
    professional = _load(db, item_id)
    on_call = body.on_call_ranges
    if on_call is None:
        on_call = _as_ranges(professional.working_hours, on_call=True)
    elif on_call and not professional.on_call_enabled:
        raise HTTPException(status_code=400, detail="On-call shifts require the professional to be available for on-call")
    _check_kinds_apart(body.ranges, on_call)
    _replace_hours(db, item_id, body.ranges)
    if body.on_call_ranges is not None:
        _replace_hours(db, item_id, body.on_call_ranges, on_call=True)
    db.commit()
    db.expire_all()
    return _hours_out(_load(db, item_id).working_hours)


# --- time off -------------------------------------------------------------------


@router.get("/{item_id}/time-off", response_model=list[TimeOffOut])
def list_time_off(
    item_id: int,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    _load(db, item_id)
    query = db.query(TimeOff).filter(TimeOff.professional_id == item_id)
    if start is not None:
        query = query.filter(TimeOff.date >= start)
    if end is not None:
        query = query.filter(TimeOff.date <= end)
    rows = query.order_by(TimeOff.date, TimeOff.start_time).all()
    return [_time_off_out(row) for row in rows]


@router.post("/{item_id}/time-off", response_model=TimeOffOut, status_code=201, dependencies=STAFF)
def create_time_off(item_id: int, body: TimeOffIn, db: Session = Depends(get_db)):
    _load(db, item_id)
    row = TimeOff(
        professional_id=item_id,
        date=body.date,
        start_time=body.start,
        end_time=body.end,
        reason=body.reason,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _time_off_out(row)


@router.delete("/{item_id}/time-off/{off_id}", status_code=204, dependencies=STAFF)
def delete_time_off(item_id: int, off_id: int, db: Session = Depends(get_db)):
    row = db.get(TimeOff, off_id)
    if row is None or row.professional_id != item_id:
        raise HTTPException(status_code=404, detail="Time off not found")
    db.delete(row)
    db.commit()


# --- calendar -------------------------------------------------------------------


@router.get("/{item_id}/slots", response_model=list[DaySlotsOut])
def get_slots(
    item_id: int,
    start: date = Query(description="First day, inclusive"),
    end: date | None = Query(default=None, description="Last day, inclusive (default: start + 6)"),
    exclude_appointment: int | None = Query(
        default=None, description="Appointment being moved: its own place does not count against the capacity"
    ),
    db: Session = Depends(get_db),
):
    """Every slot of the professional between ``start`` and ``end`` with its
    status: ``available``, ``booked`` (with the appointment), ``blocked``
    (time off) or ``full`` (free, but the day is at the professional's or the
    clinic's capacity, so it cannot be booked — see app.capacity), and
    whether it belongs to an on-call shift (``on_call``: on-call price list);
    booked slots also say whether the appointment is an urgent visit or a
    remote one (``remote``). Days outside the working hours have an empty list."""
    professional = _load(db, item_id)
    if end is None:
        end = start + timedelta(days=6)
    if end < start:
        raise HTTPException(status_code=400, detail="end must not be before start")
    if (end - start).days >= MAX_SLOT_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Range limited to {MAX_SLOT_RANGE_DAYS} days")
    time_off = (
        db.query(TimeOff)
        .filter(TimeOff.professional_id == item_id, TimeOff.date >= start, TimeOff.date <= end)
        .all()
    )
    appointments = (
        db.query(Appointment)
        .options(joinedload(Appointment.customer), joinedload(Appointment.dependent))
        .filter(
            Appointment.professional_id == item_id,
            Appointment.marked_at >= datetime.combine(start, time.min),
            Appointment.marked_at < datetime.combine(end + timedelta(days=1), time.min),
        )
        .all()
    )
    days = build_slots(
        start,
        end,
        working_hours=professional.working_hours,
        time_off=time_off,
        slot_minutes=professional.slot_minutes,
        appointments=appointments,
    )
    at_capacity = full_days(db, professional, professional.clinic, start, end, exclude_appointment)
    return [
        DaySlotsOut(
            date=day.date,
            weekday=day.weekday,
            full=day.date in at_capacity,
            full_reason=at_capacity.get(day.date, ""),
            slots=[
                SlotOut(
                    time=slot.time,
                    # A free slot of a day at capacity cannot take a booking.
                    status=SLOT_FULL if slot.status == SLOT_AVAILABLE and day.date in at_capacity else slot.status,
                    appointment_id=slot.appointment_id,
                    patient_name=slot.patient_name,
                    on_call=slot.on_call,
                    urgent=slot.urgent,
                    remote=slot.remote,
                )
                for slot in day.slots
            ],
        )
        for day in days
    ]

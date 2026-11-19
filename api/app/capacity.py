"""Service capacity of professionals and clinics.

Professionals and clinics also serve patients outside the network, so each
one can reserve only part of its agenda for Hollic Health: a maximum number of
appointments **per day** (``daily_limit``) and **per calendar month**
(``monthly_limit``); 0 means unlimited. Unlike the plan's appointment limit
(app.usage), capacity is a hard stop: once a limit is reached the system
**refuses to book** another appointment on that day/month (``409``).

What fills capacity: appointments that occupy a slot (scheduled or
completed). Cancelled ones give their place back, and cancelling is always
allowed. Capacity is checked when an appointment is (re)booked — created, or
edited onto another professional or day, or re-activated — never when notes
are edited or a visit is signed, so lowering a limit below what is already
booked never blocks record keeping (the existing appointments stand; only
new bookings are refused).

The professional's agenda (``GET /professionals/{id}/slots``) marks the free
slots of a day at capacity as ``full`` so the calendar shows at once that the
day cannot take another booking.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from .models import Appointment, Clinic, Professional
from .scheduling import occupies_slot

SLOT_FULL = "full"  # calendar status of a free slot on a day at capacity


class CapacityError(Exception):
    """The professional or the clinic cannot take another appointment."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProfessionalFull(CapacityError):
    """The professional's daily or monthly capacity is already booked."""


class ClinicFull(CapacityError):
    """The clinic's daily or monthly capacity is already booked."""


class LimitedLike(Protocol):
    name: str
    daily_limit: int
    monthly_limit: int


class AppointmentLike(Protocol):
    id: int | None
    marked_at: datetime
    status: str


@dataclass(frozen=True)
class Load:
    """How much of a payee's capacity a booking on ``day`` would find taken."""

    day: date
    day_count: int  # occupying appointments on the day
    month_count: int  # occupying appointments in the calendar month


# --- windows and counting ------------------------------------------------------


def month_window(day: date) -> tuple[date, date]:
    """First and last day of the calendar month of ``day``."""
    start = day.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def count_occupying(rows: Iterable[AppointmentLike], start: date, end: date, exclude_id: int | None = None) -> int:
    """Appointments taking capacity (scheduled/completed) dated in [start, end]."""
    total = 0
    for row in rows:
        if exclude_id is not None and row.id == exclude_id:
            continue
        if occupies_slot(row.status) and start <= row.marked_at.date() <= end:
            total += 1
    return total


def load_of(rows: Iterable[AppointmentLike], day: date, exclude_id: int | None = None) -> Load:
    rows = list(rows)
    month_start, month_end = month_window(day)
    return Load(
        day=day,
        day_count=count_occupying(rows, day, day, exclude_id),
        month_count=count_occupying(rows, month_start, month_end, exclude_id),
    )


def is_full(limited: LimitedLike, load: Load) -> bool:
    """Whether one more appointment would exceed a limit of the payee."""
    if limited.daily_limit and load.day_count >= limited.daily_limit:
        return True
    if limited.monthly_limit and load.month_count >= limited.monthly_limit:
        return True
    return False


def assert_capacity(kind: str, limited: LimitedLike, load: Load) -> None:
    """Raise ``CapacityError`` when the payee cannot take one more on the day."""
    error = ProfessionalFull if kind == "professional" else ClinicFull
    if limited.daily_limit and load.day_count >= limited.daily_limit:
        raise error(
            f"{kind.capitalize()} {limited.name} is fully booked on {load.day:%Y-%m-%d}: "
            f"{load.day_count} of {limited.daily_limit} appointments per day"
        )
    if limited.monthly_limit and load.month_count >= limited.monthly_limit:
        raise error(
            f"{kind.capitalize()} {limited.name} is fully booked for {load.day:%Y-%m}: "
            f"{load.month_count} of {limited.monthly_limit} appointments per month"
        )


# --- database -----------------------------------------------------------------


def _rows_between(db: Session, start: date, end: date, *, professional_id: int | None = None, clinic_id: int | None = None):
    query = db.query(Appointment).filter(
        Appointment.marked_at >= datetime.combine(start, time.min),
        Appointment.marked_at < datetime.combine(end + timedelta(days=1), time.min),
    )
    if professional_id is not None:
        query = query.filter(Appointment.professional_id == professional_id)
    if clinic_id is not None:
        query = query.filter(Appointment.clinic_id == clinic_id)
    return query.all()


def professional_load(db: Session, professional_id: int, day: date, exclude_id: int | None = None) -> Load:
    start, end = month_window(day)
    return load_of(_rows_between(db, start, end, professional_id=professional_id), day, exclude_id)


def clinic_load(db: Session, clinic_id: int, day: date, exclude_id: int | None = None) -> Load:
    start, end = month_window(day)
    return load_of(_rows_between(db, start, end, clinic_id=clinic_id), day, exclude_id)


def check_capacity(db: Session, professional: Professional, clinic: Clinic, day: date, exclude_id: int | None = None) -> None:
    """Raise ``CapacityError`` unless both the professional and its clinic can
    take one more appointment on ``day`` (``exclude_id``: the appointment
    being edited does not count against its own booking)."""
    if professional.daily_limit or professional.monthly_limit:
        assert_capacity("professional", professional, professional_load(db, professional.id, day, exclude_id))
    if clinic.daily_limit or clinic.monthly_limit:
        assert_capacity("clinic", clinic, clinic_load(db, clinic.id, day, exclude_id))


def full_days(
    db: Session, professional: Professional, clinic: Clinic, start: date, end: date, exclude_id: int | None = None
) -> dict[date, str]:
    """The days of [start, end] that cannot take another booking, with the
    reason (for the agenda): ``professional`` or ``clinic``. ``exclude_id``
    is the appointment being moved, whose own place does not count — the same
    rule the booking check applies."""
    result: dict[date, str] = {}
    if not any((professional.daily_limit, professional.monthly_limit, clinic.daily_limit, clinic.monthly_limit)):
        return result
    span_start, _ = month_window(start)
    _, span_end = month_window(end)
    own = _rows_between(db, span_start, span_end, professional_id=professional.id)
    shared = _rows_between(db, span_start, span_end, clinic_id=clinic.id)
    cursor = start
    while cursor <= end:
        if is_full(professional, load_of(own, cursor, exclude_id)):
            result[cursor] = "professional"
        elif is_full(clinic, load_of(shared, cursor, exclude_id)):
            result[cursor] = "clinic"
        cursor += timedelta(days=1)
    return result

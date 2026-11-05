"""Slot rules for appointments.

A professional works in recurring weekly hour ranges (``WorkingHours``) split
into slots of ``Professional.slot_minutes``. Specific dates or time ranges can
be taken off (``TimeOff``). A slot is identified by the professional, the
calendar date and the time of day expressed as ``HH:mm``; seconds,
microseconds and timezone offsets are not part of a slot and ``marked_at`` is
normalized to the wall-clock minute before being compared or stored.

A range may be an **on-call shift** (``on_call``): its slots are booked like
any other, but the appointment is an on-call service priced at the on-call
rates (see app.pricing). Regular and on-call ranges of a weekday never
overlap, so every slot has exactly one kind.

**Urgent visits** (``urgent``) are ad hoc: a professional who offers them
(``urgent_enabled``) can be booked at any time — inside or outside the
working hours — except during an on-call shift, where urgent visits are
never accepted. Time off, the conflict rules and the capacity still apply.

**Remote appointments** (``remote``, a video call) are booked like in-person
ones — a slot of the regular working hours, same time off, conflicts and
capacity — by a professional who offers them (``remote_enabled``). They are
refused inside an on-call shift and are never urgent, so each appointment
has exactly one price list (see app.pricing and app.remote).

Rules enforced when an appointment is created or edited:

* the time must be one of the professional's slots for that weekday and the
  professional must not be off at that time;
* the professional cannot have two appointments in the same slot;
* the same patient (holder or dependent) cannot be with two professionals
  in the same slot.

Only appointments whose status occupies a slot (``agendada``/``realizada``)
take part in these checks. A ``cancelada`` appointment frees its slot and
may itself be saved at any time, so cancelling is always possible even after
the professional's hours have changed.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from .models import Appointment, Professional, TimeOff, WorkingHours

OCCUPYING_STATUSES = frozenset({"agendada", "realizada"})
TIME_FORMAT = "%H:%M"
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

DEFAULT_SLOT_MINUTES = 30
# Monday to Friday, mornings and afternoons. Applied to professionals created
# without explicit hours; each professional can then adjust their own week.
DEFAULT_WEEK: tuple[tuple[int, str, str], ...] = tuple(
    (weekday, start, end) for weekday in range(5) for start, end in (("08:00", "12:00"), ("13:00", "17:00"))
)

SLOT_AVAILABLE = "available"
SLOT_BOOKED = "booked"
SLOT_BLOCKED = "blocked"


class SlotError(Exception):
    """The appointment cannot take the requested slot."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProfessionalUnavailable(SlotError):
    """The weekday/time is not a working slot of the professional, or is taken off."""

    status_code = 400


class ProfessionalBusy(SlotError):
    """The professional already has an appointment in the slot."""


class CustomerBusy(SlotError):
    """The patient already has an appointment with another professional in the slot."""


class UrgentNotOffered(SlotError):
    """The professional does not take ad-hoc urgent visits."""

    status_code = 400


class UrgentDuringOnCall(SlotError):
    """Urgent visits are not accepted while the professional is on call."""


class RemoteNotOffered(SlotError):
    """The professional does not offer remote appointments, or the request
    combines remote with an urgent visit."""

    status_code = 400


class RemoteDuringOnCall(SlotError):
    """Remote appointments are not accepted inside an on-call shift."""


class WorkingHoursLike(Protocol):
    weekday: int
    start_time: str
    end_time: str
    # on_call: bool  (optional; absent means a regular range)


class TimeOffLike(Protocol):
    date: date
    start_time: str | None
    end_time: str | None


class AppointmentLike(Protocol):
    id: int | None
    professional_id: int
    customer_id: int
    dependent_id: int | None
    status: str


@dataclass(frozen=True)
class Slot:
    time: str
    status: str
    appointment_id: int | None = None
    patient_name: str | None = None
    on_call: bool = False  # the slot belongs to an on-call shift
    urgent: bool = False  # an ad-hoc urgent visit (may sit outside the grid)
    remote: bool = False  # a booked remote appointment (video call)


@dataclass(frozen=True)
class DaySlots:
    date: date
    weekday: int
    slots: tuple[Slot, ...]


# --- time helpers -------------------------------------------------------------


def occupies_slot(status: str) -> bool:
    return status in OCCUPYING_STATUSES


def normalize_marked_at(value: datetime) -> datetime:
    """Drop timezone, seconds and microseconds so the time is exactly HH:mm."""
    return value.replace(tzinfo=None, second=0, microsecond=0)


def slot_time(value: datetime) -> str:
    return value.strftime(TIME_FORMAT)


def slot_window(value: datetime) -> tuple[datetime, datetime]:
    """Half-open [start, end) range covering every instant of the slot minute."""
    start = normalize_marked_at(value)
    return start, start + timedelta(minutes=1)


def minutes_of(time_text: str) -> int:
    hours, minutes = time_text.split(":")
    return int(hours) * 60 + int(minutes)


def text_of(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# --- working hours and time off ----------------------------------------------


def is_on_call_range(span: WorkingHoursLike) -> bool:
    return bool(getattr(span, "on_call", False))


def day_slot_times(
    working_hours: Iterable[WorkingHoursLike], weekday: int, slot_minutes: int, on_call: bool | None = None
) -> list[str]:
    """Every slot start (HH:mm) for a weekday: ranges cut into whole slots.
    ``on_call`` restricts to on-call (True) or regular (False) ranges; None
    takes both."""
    if slot_minutes <= 0:
        raise ValueError("slot_minutes must be positive")
    times: set[int] = set()
    for span in working_hours:
        if span.weekday != weekday:
            continue
        if on_call is not None and is_on_call_range(span) != on_call:
            continue
        start, end = minutes_of(span.start_time), minutes_of(span.end_time)
        cursor = start
        while cursor + slot_minutes <= end:
            times.add(cursor)
            cursor += slot_minutes
    return [text_of(item) for item in sorted(times)]


def is_working_time(
    working_hours: Iterable[WorkingHoursLike], marked_at: datetime, slot_minutes: int
) -> bool:
    return slot_time(marked_at) in day_slot_times(working_hours, marked_at.weekday(), slot_minutes)


def is_on_call_time(
    working_hours: Iterable[WorkingHoursLike], marked_at: datetime, slot_minutes: int
) -> bool:
    """Whether the slot belongs to an on-call shift of the professional."""
    return slot_time(marked_at) in day_slot_times(working_hours, marked_at.weekday(), slot_minutes, on_call=True)


def is_time_off(time_off: Iterable[TimeOffLike], marked_at: datetime) -> bool:
    """True when some time-off entry covers the date and HH:mm of ``marked_at``."""
    minute = minutes_of(slot_time(marked_at))
    for item in time_off:
        if item.date != marked_at.date():
            continue
        if item.start_time is None or item.end_time is None:
            return True  # whole day
        if minutes_of(item.start_time) <= minute < minutes_of(item.end_time):
            return True
    return False


def assert_professional_available(
    working_hours: Iterable[WorkingHoursLike],
    time_off: Iterable[TimeOffLike],
    slot_minutes: int,
    marked_at: datetime,
) -> None:
    when = f"{WEEKDAY_NAMES[marked_at.weekday()]} at {slot_time(marked_at)}"
    if not is_working_time(working_hours, marked_at, slot_minutes):
        raise ProfessionalUnavailable(f"Professional does not work on {when}")
    if is_time_off(time_off, marked_at):
        raise ProfessionalUnavailable(
            f"Professional is off on {marked_at:%Y-%m-%d} at {slot_time(marked_at)}"
        )


def assert_urgent_allowed(
    urgent_enabled: bool,
    working_hours: Iterable[WorkingHoursLike],
    time_off: Iterable[TimeOffLike],
    slot_minutes: int,
    marked_at: datetime,
) -> None:
    """An urgent visit needs a professional who offers them, outside any
    on-call shift and not in a time off; working hours are not required."""
    when = f"{WEEKDAY_NAMES[marked_at.weekday()]} at {slot_time(marked_at)}"
    if not urgent_enabled:
        raise UrgentNotOffered("Professional does not take urgent visits")
    if is_on_call_time(working_hours, marked_at, slot_minutes):
        raise UrgentDuringOnCall(f"Professional is on call on {when}: urgent visits are not accepted during on-call shifts")
    if is_time_off(time_off, marked_at):
        raise ProfessionalUnavailable(f"Professional is off on {marked_at:%Y-%m-%d} at {slot_time(marked_at)}")


def assert_remote_allowed(
    remote_enabled: bool,
    working_hours: Iterable[WorkingHoursLike],
    slot_minutes: int,
    marked_at: datetime,
    urgent: bool = False,
) -> None:
    """A remote appointment needs a professional who offers them, is never
    an urgent visit and never sits in an on-call shift. The regular rules
    (a working slot, no time off) are checked separately."""
    when = f"{WEEKDAY_NAMES[marked_at.weekday()]} at {slot_time(marked_at)}"
    if not remote_enabled:
        raise RemoteNotOffered("Professional does not offer remote appointments")
    if urgent:
        raise RemoteNotOffered("Urgent visits are attended in person: a remote appointment cannot be urgent")
    if is_on_call_time(working_hours, marked_at, slot_minutes):
        raise RemoteDuringOnCall(f"Professional is on call on {when}: remote appointments are not accepted during on-call shifts")


# --- conflicts ----------------------------------------------------------------


def same_patient(row: AppointmentLike, customer_id: int, dependent_id: int | None) -> bool:
    """The holder (dependent_id None) and each dependent are distinct patients."""
    return row.customer_id == customer_id and row.dependent_id == dependent_id


def assert_no_conflicts(
    same_slot: Iterable[AppointmentLike],
    *,
    professional_id: int,
    customer_id: int,
    dependent_id: int | None,
    marked_at: datetime,
    exclude_id: int | None = None,
) -> None:
    """Check appointments already in the slot against the one being saved.

    ``same_slot`` holds the appointments that share the date and HH:mm with
    ``marked_at`` (any professional, any customer). ``exclude_id`` is the id
    of the appointment being edited so it does not collide with itself.
    """
    when = f"{marked_at:%Y-%m-%d} {slot_time(marked_at)}"
    for row in same_slot:
        if exclude_id is not None and row.id == exclude_id:
            continue
        if not occupies_slot(row.status):
            continue
        if row.professional_id == professional_id:
            raise ProfessionalBusy(f"Professional already has an appointment at {when}")
        if same_patient(row, customer_id, dependent_id):
            raise CustomerBusy(
                f"Customer already has an appointment with another professional at {when}"
            )


# --- calendar -----------------------------------------------------------------


def build_day_slots(
    day: date,
    *,
    working_hours: Iterable[WorkingHoursLike],
    time_off: Iterable[TimeOffLike],
    slot_minutes: int,
    appointments: Iterable[Appointment],
) -> DaySlots:
    """Slots of one day with their status. ``appointments`` are the
    professional's rows on that day (any status; cancelled ones are ignored)."""
    booked: dict[str, Appointment] = {}
    for row in appointments:
        if occupies_slot(row.status) and row.marked_at.date() == day:
            booked[slot_time(row.marked_at)] = row
    working_hours = list(working_hours)
    on_call_times = set(day_slot_times(working_hours, day.weekday(), slot_minutes, on_call=True))
    slots = []
    grid = day_slot_times(working_hours, day.weekday(), slot_minutes)
    for time_text in grid:
        moment = datetime.combine(day, datetime.strptime(time_text, TIME_FORMAT).time())
        on_call = time_text in on_call_times
        if time_text in booked:
            row = booked[time_text]
            patient = row.dependent.name if row.dependent else row.customer.name
            slots.append(
                Slot(time_text, SLOT_BOOKED, row.id, patient, on_call, bool(getattr(row, "urgent", False)), bool(getattr(row, "remote", False)))
            )
        elif is_time_off(time_off, moment):
            slots.append(Slot(time_text, SLOT_BLOCKED, on_call=on_call))
        else:
            slots.append(Slot(time_text, SLOT_AVAILABLE, on_call=on_call))
    # Ad-hoc visits outside the grid (urgent ones) still show on the day.
    for time_text, row in booked.items():
        if time_text not in grid:
            patient = row.dependent.name if row.dependent else row.customer.name
            slots.append(
                Slot(time_text, SLOT_BOOKED, row.id, patient, False, bool(getattr(row, "urgent", False)), bool(getattr(row, "remote", False)))
            )
    slots.sort(key=lambda slot: slot.time)
    return DaySlots(date=day, weekday=day.weekday(), slots=tuple(slots))


def build_slots(
    start: date,
    end: date,
    *,
    working_hours: Iterable[WorkingHoursLike],
    time_off: Iterable[TimeOffLike],
    slot_minutes: int,
    appointments: Iterable[Appointment],
) -> list[DaySlots]:
    """Slots for every day in the closed range [start, end]."""
    working_hours = list(working_hours)
    time_off = list(time_off)
    appointments = list(appointments)
    days = []
    cursor = start
    while cursor <= end:
        days.append(
            build_day_slots(
                cursor,
                working_hours=working_hours,
                time_off=time_off,
                slot_minutes=slot_minutes,
                appointments=appointments,
            )
        )
        cursor += timedelta(days=1)
    return days


# --- database entry points ---------------------------------------------------


def professional_hours(db: Session, professional_id: int) -> list[WorkingHours]:
    return db.query(WorkingHours).filter(WorkingHours.professional_id == professional_id).all()


def on_call_slot(db: Session, professional: Professional, marked_at: datetime) -> bool:
    """Whether ``marked_at`` falls in an on-call shift of the professional
    (only when the professional is available for on-call)."""
    if not professional.on_call_enabled:
        return False
    return is_on_call_time(professional_hours(db, professional.id), normalize_marked_at(marked_at), professional.slot_minutes)


def appointments_in_slot(db: Session, marked_at: datetime) -> list[Appointment]:
    start, end = slot_window(marked_at)
    return (
        db.query(Appointment)
        .filter(Appointment.marked_at >= start, Appointment.marked_at < end)
        .all()
    )


def check_slot(
    db: Session,
    *,
    professional: Professional,
    customer_id: int,
    dependent_id: int | None,
    marked_at: datetime,
    status: str,
    exclude_id: int | None = None,
    urgent: bool = False,
    remote: bool = False,
) -> None:
    """Raise ``SlotError`` if the appointment cannot be saved in this slot.

    Nothing is checked when ``status`` does not occupy a slot. An urgent
    visit skips the working-hours rule but must be outside any on-call shift.
    A remote appointment follows the regular rules and must be offered by
    the professional, outside any on-call shift and not urgent.
    """
    if not occupies_slot(status):
        return
    marked_at = normalize_marked_at(marked_at)
    working_hours = professional_hours(db, professional.id)
    time_off = (
        db.query(TimeOff)
        .filter(TimeOff.professional_id == professional.id, TimeOff.date == marked_at.date())
        .all()
    )
    if remote:
        assert_remote_allowed(bool(professional.remote_enabled), working_hours, professional.slot_minutes, marked_at, urgent)
    if urgent:
        assert_urgent_allowed(bool(professional.urgent_enabled), working_hours, time_off, professional.slot_minutes, marked_at)
    else:
        assert_professional_available(working_hours, time_off, professional.slot_minutes, marked_at)
    assert_no_conflicts(
        appointments_in_slot(db, marked_at),
        professional_id=professional.id,
        customer_id=customer_id,
        dependent_id=dependent_id,
        marked_at=marked_at,
        exclude_id=exclude_id,
    )

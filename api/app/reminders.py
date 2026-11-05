"""Appointment reminders: one day before and one hour before the slot.

``schedule_reminders`` is idempotent: an appointment is stamped
(``reminded_day_at`` / ``reminded_hour_at``) when its reminder is created, so
running it every minute never repeats a reminder. Moving an appointment
clears the stamps (see the appointments router) so the new slot is reminded
again. Only scheduled (``agendada``) appointments are reminded.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from . import clock
from .models import Appointment
from .notifications import appointment_reminder

DAY_BEFORE = timedelta(hours=24)
HOUR_BEFORE = timedelta(hours=1)
# A reminder is not sent when the worker was down and the slot is closer than this.
MIN_LEAD = timedelta(minutes=5)


def due_for_reminder(row: Appointment, now: datetime, lead: timedelta, stamp: datetime | None) -> bool:
    """Slot inside the lead window, still in the future and not yet reminded."""
    if row.status != "agendada" or stamp is not None:
        return False
    remaining = row.marked_at - now
    return MIN_LEAD <= remaining <= lead


def schedule_reminders(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or clock.now()
    horizon = now + DAY_BEFORE
    rows = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.customer),
            joinedload(Appointment.dependent),
            joinedload(Appointment.professional),
            joinedload(Appointment.specialty),
            joinedload(Appointment.clinic),
        )
        .filter(Appointment.status == "agendada", Appointment.marked_at > now, Appointment.marked_at <= horizon)
        .all()
    )
    counts = {"day": 0, "hour": 0}
    for row in rows:
        if due_for_reminder(row, now, DAY_BEFORE, row.reminded_day_at):
            appointment_reminder(db, row, 24)
            row.reminded_day_at = now
            counts["day"] += 1
        if due_for_reminder(row, now, HOUR_BEFORE, row.reminded_hour_at):
            appointment_reminder(db, row, 1)
            row.reminded_hour_at = now
            counts["hour"] += 1
    db.commit()
    return counts

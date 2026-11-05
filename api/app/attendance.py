"""Check-in / check-out of appointments (attendance audit).

The patient (or the reception) confirms arrival with a **check-in**, allowed
from ``CHECKIN_OPENS_BEFORE`` before the slot until ``CHECKIN_CLOSES_AFTER``
after it, and confirms the end of the visit with a **check-out**. Both record
who did it, when and (optionally) the device position. A scheduled
appointment whose slot passed without a check-in is a **no-show**.

Between the check-in and the check-out the visit is **started** with the
code of the patient's virtual card (``app.virtual_card``), which proves the
patient is the right person; a started appointment is *in progress*.

The check-out does not complete the appointment: completion is the
professional's **signature** (see ``app.signature``). An appointment checked
out but not signed is *awaiting signature*.
"""

import os
from datetime import datetime, timedelta

CHECKIN_OPENS_BEFORE = timedelta(minutes=int(os.getenv("CHECKIN_OPENS_BEFORE_MINUTES", "120")))
CHECKIN_CLOSES_AFTER = timedelta(minutes=int(os.getenv("CHECKIN_CLOSES_AFTER_MINUTES", "120")))
NO_SHOW_AFTER = CHECKIN_CLOSES_AFTER


class AttendanceError(Exception):
    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def checkin_window(marked_at: datetime) -> tuple[datetime, datetime]:
    return marked_at - CHECKIN_OPENS_BEFORE, marked_at + CHECKIN_CLOSES_AFTER


def can_check_in(row, now: datetime) -> bool:
    if row.status != "agendada" or row.checked_in_at is not None:
        return False
    opens, closes = checkin_window(row.marked_at)
    return opens <= now <= closes


def can_check_out(row, now: datetime) -> bool:
    return row.status == "agendada" and row.checked_in_at is not None and row.checked_out_at is None


def is_no_show(row, now: datetime) -> bool:
    return row.status == "agendada" and row.checked_in_at is None and now > row.marked_at + NO_SHOW_AFTER


def attendance_state(row, now: datetime) -> str:
    """checked_out | completed | in_progress | checked_in | no_show | awaiting | cancelled"""
    if row.status == "cancelada":
        return "cancelled"
    if row.checked_out_at is not None:
        return "checked_out"
    if row.status == "realizada":
        return "completed"  # signed by the professional without the check-out flow
    if getattr(row, "started_at", None) is not None:
        return "in_progress"  # patient verified with the card, visit under way
    if row.checked_in_at is not None:
        return "checked_in"
    if is_no_show(row, now):
        return "no_show"
    return "awaiting"


def assert_can_check_in(row, now: datetime) -> None:
    if row.status == "cancelada":
        raise AttendanceError("Appointment is cancelled")
    if row.status == "realizada":
        raise AttendanceError("Appointment is already completed")
    if row.checked_in_at is not None:
        raise AttendanceError(f"Already checked in at {row.checked_in_at:%d/%m/%Y %H:%M}")
    opens, closes = checkin_window(row.marked_at)
    if now < opens:
        raise AttendanceError(f"Check-in opens at {opens:%d/%m/%Y %H:%M}")
    if now > closes:
        raise AttendanceError(f"Check-in closed at {closes:%d/%m/%Y %H:%M}; contact the reception")


def assert_can_check_out(row, now: datetime) -> None:
    if row.status == "cancelada":
        raise AttendanceError("Appointment is cancelled")
    if row.checked_out_at is not None:
        raise AttendanceError(f"Already checked out at {row.checked_out_at:%d/%m/%Y %H:%M}")
    if row.status == "realizada":
        raise AttendanceError("Appointment is already completed")
    if row.checked_in_at is None:
        raise AttendanceError("Check in before checking out")
    if now < row.checked_in_at:
        raise AttendanceError("Check-out cannot precede the check-in")

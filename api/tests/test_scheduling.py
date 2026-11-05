"""Unit tests for the pure slot rules in app.scheduling (no HTTP, no database)."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.scheduling import (
    DEFAULT_SLOT_MINUTES,
    DEFAULT_WEEK,
    OCCUPYING_STATUSES,
    SLOT_AVAILABLE,
    SLOT_BLOCKED,
    SLOT_BOOKED,
    CustomerBusy,
    ProfessionalBusy,
    ProfessionalUnavailable,
    SlotError,
    assert_no_conflicts,
    assert_professional_available,
    build_day_slots,
    build_slots,
    day_slot_times,
    is_time_off,
    is_working_time,
    minutes_of,
    normalize_marked_at,
    occupies_slot,
    slot_time,
    slot_window,
    text_of,
)

MONDAY = datetime(2026, 10, 26, 8, 0)  # 2026-10-26 is a Monday
TUESDAY = MONDAY + timedelta(days=1)


@dataclass
class Hours:
    weekday: int
    start_time: str
    end_time: str


@dataclass
class Off:
    date: date
    start_time: str | None = None
    end_time: str | None = None


@dataclass
class Appt:
    id: int
    professional_id: int
    customer_id: int
    dependent_id: int | None = None
    status: str = "agendada"
    marked_at: datetime = MONDAY
    customer: object = field(default_factory=lambda: SimpleNamespace(name="Holder"))
    dependent: object | None = None


MORNING = [Hours(0, "08:00", "12:00")]


# --- statuses -----------------------------------------------------------------


def test_occupying_statuses_are_scheduled_and_completed():
    assert OCCUPYING_STATUSES == {"agendada", "realizada"}
    assert occupies_slot("agendada") is True
    assert occupies_slot("realizada") is True
    assert occupies_slot("cancelada") is False


def test_slot_error_status_codes():
    assert ProfessionalUnavailable("x").status_code == 400
    assert ProfessionalBusy("x").status_code == 409
    assert CustomerBusy("x").status_code == 409
    for kind in (ProfessionalUnavailable, ProfessionalBusy, CustomerBusy):
        assert issubclass(kind, SlotError)


# --- HH:mm normalization ------------------------------------------------------


def test_normalize_drops_seconds_and_microseconds():
    assert normalize_marked_at(datetime(2026, 10, 26, 8, 30, 45, 123456)) == datetime(2026, 10, 26, 8, 30)


def test_normalize_drops_timezone_but_keeps_wall_clock_time():
    value = datetime(2026, 10, 26, 8, 30, tzinfo=timezone(timedelta(hours=-3)))
    normalized = normalize_marked_at(value)
    assert normalized.tzinfo is None
    assert normalized == datetime(2026, 10, 26, 8, 30)


def test_slot_time_is_hh_mm():
    assert slot_time(datetime(2026, 10, 26, 8, 5, 59)) == "08:05"
    assert slot_time(datetime(2026, 10, 26, 0, 0)) == "00:00"


def test_slot_window_covers_the_whole_minute():
    start, end = slot_window(datetime(2026, 10, 26, 8, 0, 30))
    assert (start, end) == (datetime(2026, 10, 26, 8, 0), datetime(2026, 10, 26, 8, 1))


def test_minutes_round_trip():
    assert minutes_of("08:30") == 510
    assert text_of(510) == "08:30"
    assert text_of(0) == "00:00"
    assert minutes_of("23:59") == 1439


# --- default week -------------------------------------------------------------


def test_default_week_is_monday_to_friday_mornings_and_afternoons():
    assert DEFAULT_SLOT_MINUTES == 30
    assert {w for w, _, _ in DEFAULT_WEEK} == {0, 1, 2, 3, 4}
    for weekday in range(5):
        ranges = sorted((s, e) for w, s, e in DEFAULT_WEEK if w == weekday)
        assert ranges == [("08:00", "12:00"), ("13:00", "17:00")]


# --- slots from working hours -------------------------------------------------


def test_day_slot_times_cuts_ranges_into_whole_slots():
    assert day_slot_times(MORNING, 0, 30) == [
        "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    ]  # fmt: skip
    assert day_slot_times(MORNING, 0, 60) == ["08:00", "09:00", "10:00", "11:00"]


def test_day_slot_times_drops_partial_slot_at_the_end():
    assert day_slot_times([Hours(0, "08:00", "09:20")], 0, 30) == ["08:00", "08:30"]


def test_day_slot_times_merges_multiple_ranges_of_the_weekday():
    hours = [Hours(0, "13:00", "14:00"), Hours(0, "08:00", "09:00")]
    assert day_slot_times(hours, 0, 30) == ["08:00", "08:30", "13:00", "13:30"]


def test_day_slot_times_only_for_requested_weekday():
    hours = [Hours(0, "08:00", "09:00"), Hours(2, "14:00", "15:00")]
    assert day_slot_times(hours, 2, 30) == ["14:00", "14:30"]
    assert day_slot_times(hours, 1, 30) == []
    assert day_slot_times([], 0, 30) == []


def test_day_slot_times_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        day_slot_times(MORNING, 0, 0)


def test_working_time_must_match_the_slot_grid():
    assert is_working_time(MORNING, MONDAY, 30) is True
    assert is_working_time(MORNING, MONDAY.replace(minute=30), 30) is True
    assert is_working_time(MORNING, MONDAY.replace(minute=15), 30) is False  # off grid
    assert is_working_time(MORNING, MONDAY.replace(minute=30), 60) is False  # 60-min grid
    assert is_working_time(MORNING, MONDAY.replace(hour=12), 30) is False  # end is exclusive
    assert is_working_time(MORNING, MONDAY.replace(hour=11, minute=30), 30) is True


def test_working_time_on_other_weekday_or_week():
    assert is_working_time(MORNING, TUESDAY, 30) is False
    assert is_working_time(MORNING, MONDAY + timedelta(weeks=4), 30) is True


def test_working_time_compares_hh_mm_only():
    assert is_working_time(MORNING, MONDAY.replace(second=59), 30) is True


# --- time off -----------------------------------------------------------------


def test_whole_day_off_blocks_every_time():
    off = [Off(MONDAY.date())]
    assert is_time_off(off, MONDAY) is True
    assert is_time_off(off, MONDAY.replace(hour=16)) is True
    assert is_time_off(off, TUESDAY) is False


def test_partial_day_off_blocks_only_its_range():
    off = [Off(MONDAY.date(), "09:00", "10:00")]
    assert is_time_off(off, MONDAY.replace(hour=8, minute=30)) is False
    assert is_time_off(off, MONDAY.replace(hour=9)) is True
    assert is_time_off(off, MONDAY.replace(hour=9, minute=30)) is True
    assert is_time_off(off, MONDAY.replace(hour=10)) is False  # end is exclusive


def test_time_off_is_date_specific():
    off = [Off(MONDAY.date(), "08:00", "09:00")]
    assert is_time_off(off, MONDAY + timedelta(weeks=1)) is False


# --- availability assertion ---------------------------------------------------


def test_assert_available_passes_silently():
    assert_professional_available(MORNING, [], 30, MONDAY)


def test_assert_available_rejects_non_working_time_with_weekday_in_message():
    with pytest.raises(ProfessionalUnavailable) as info:
        assert_professional_available(MORNING, [], 30, TUESDAY.replace(hour=15, minute=30))
    assert "Tuesday" in info.value.message
    assert "15:30" in info.value.message


def test_assert_available_rejects_time_off_with_date_in_message():
    with pytest.raises(ProfessionalUnavailable) as info:
        assert_professional_available(MORNING, [Off(MONDAY.date())], 30, MONDAY)
    assert "off" in info.value.message
    assert "2026-10-26" in info.value.message


# --- conflicts ----------------------------------------------------------------


def check(rows, *, professional_id=1, customer_id=10, dependent_id=None, exclude_id=None):
    assert_no_conflicts(
        rows,
        professional_id=professional_id,
        customer_id=customer_id,
        dependent_id=dependent_id,
        marked_at=MONDAY,
        exclude_id=exclude_id,
    )


def test_no_conflict_when_slot_is_empty():
    check([])


def test_professional_cannot_have_two_appointments_in_the_slot():
    with pytest.raises(ProfessionalBusy):
        check([Appt(1, professional_id=1, customer_id=99)], professional_id=1)


def test_completed_appointment_still_occupies_the_slot():
    with pytest.raises(ProfessionalBusy):
        check([Appt(1, professional_id=1, customer_id=99, status="realizada")])


def test_cancelled_appointment_frees_the_slot():
    check([Appt(1, professional_id=1, customer_id=10, status="cancelada")])


def test_customer_cannot_be_with_two_professionals_in_the_slot():
    with pytest.raises(CustomerBusy):
        check([Appt(1, professional_id=2, customer_id=10)], professional_id=1, customer_id=10)


def test_customer_conflict_ignores_cancelled_appointments():
    check([Appt(1, professional_id=2, customer_id=10, status="cancelada")], customer_id=10)


def test_different_professionals_and_customers_share_the_slot():
    check([Appt(1, professional_id=2, customer_id=99)], professional_id=1, customer_id=10)


def test_same_dependent_cannot_be_with_two_professionals():
    with pytest.raises(CustomerBusy):
        check(
            [Appt(1, professional_id=2, customer_id=10, dependent_id=5)],
            professional_id=1,
            customer_id=10,
            dependent_id=5,
        )


def test_holder_and_dependent_are_distinct_patients():
    check([Appt(1, professional_id=2, customer_id=10)], professional_id=1, customer_id=10, dependent_id=5)
    check([Appt(1, professional_id=2, customer_id=10, dependent_id=5)], professional_id=1, customer_id=10)


def test_edited_appointment_does_not_conflict_with_itself():
    check([Appt(7, professional_id=1, customer_id=10)], exclude_id=7)


def test_edited_appointment_still_conflicts_with_others():
    rows = [Appt(7, professional_id=1, customer_id=10), Appt(8, professional_id=1, customer_id=99)]
    with pytest.raises(ProfessionalBusy):
        check(rows, exclude_id=7)


def test_conflict_messages_include_date_and_hh_mm():
    with pytest.raises(ProfessionalBusy) as busy:
        check([Appt(1, professional_id=1, customer_id=99)])
    assert "2026-10-26 08:00" in busy.value.message
    with pytest.raises(CustomerBusy) as taken:
        check([Appt(1, professional_id=2, customer_id=10)])
    assert "2026-10-26 08:00" in taken.value.message


# --- calendar -----------------------------------------------------------------


def test_build_day_slots_marks_available_booked_and_blocked():
    hours = [Hours(0, "08:00", "10:00")]
    off = [Off(MONDAY.date(), "09:00", "09:30")]
    booked = Appt(5, 1, 10, marked_at=MONDAY.replace(minute=30))
    day = build_day_slots(
        MONDAY.date(), working_hours=hours, time_off=off, slot_minutes=30, appointments=[booked]
    )
    assert day.weekday == 0
    assert [(s.time, s.status) for s in day.slots] == [
        ("08:00", SLOT_AVAILABLE),
        ("08:30", SLOT_BOOKED),
        ("09:00", SLOT_BLOCKED),
        ("09:30", SLOT_AVAILABLE),
    ]
    assert day.slots[1].appointment_id == 5
    assert day.slots[1].patient_name == "Holder"


def test_build_day_slots_names_the_dependent_when_present():
    booked = Appt(5, 1, 10, dependent_id=3, dependent=SimpleNamespace(name="Child"))
    day = build_day_slots(
        MONDAY.date(), working_hours=MORNING, time_off=[], slot_minutes=30, appointments=[booked]
    )
    assert day.slots[0].patient_name == "Child"


def test_build_day_slots_ignores_cancelled_and_other_days():
    cancelled = Appt(1, 1, 10, status="cancelada")
    other_day = Appt(2, 1, 10, marked_at=MONDAY + timedelta(weeks=1))
    day = build_day_slots(
        MONDAY.date(),
        working_hours=MORNING,
        time_off=[],
        slot_minutes=30,
        appointments=[cancelled, other_day],
    )
    assert all(s.status == SLOT_AVAILABLE for s in day.slots)


def test_build_day_slots_whole_day_off_blocks_everything():
    day = build_day_slots(
        MONDAY.date(), working_hours=MORNING, time_off=[Off(MONDAY.date())], slot_minutes=30, appointments=[]
    )
    assert len(day.slots) == 8
    assert all(s.status == SLOT_BLOCKED for s in day.slots)


def test_build_day_slots_booked_wins_over_blocked():
    # Time off added after the appointment was made: the slot still shows the appointment.
    booked = Appt(5, 1, 10)
    day = build_day_slots(
        MONDAY.date(),
        working_hours=MORNING,
        time_off=[Off(MONDAY.date())],
        slot_minutes=30,
        appointments=[booked],
    )
    assert day.slots[0].status == SLOT_BOOKED


def test_build_slots_covers_every_day_of_the_range_inclusive():
    days = build_slots(
        MONDAY.date(),
        MONDAY.date() + timedelta(days=6),
        working_hours=MORNING,
        time_off=[],
        slot_minutes=30,
        appointments=[],
    )
    assert [d.date for d in days] == [MONDAY.date() + timedelta(days=i) for i in range(7)]
    assert len(days[0].slots) == 8
    assert all(d.slots == () for d in days[1:])  # no hours on other weekdays


# --- on-call shifts -------------------------------------------------------------


@dataclass
class OnCallHours(Hours):
    on_call: bool = True


WEEK_WITH_SHIFT = [Hours(0, "08:00", "12:00"), OnCallHours(0, "19:00", "22:00"), OnCallHours(5, "08:00", "12:00")]


def test_day_slot_times_can_select_the_kind_of_range():
    from app.scheduling import is_on_call_time

    assert day_slot_times(WEEK_WITH_SHIFT, 0, 60) == ["08:00", "09:00", "10:00", "11:00", "19:00", "20:00", "21:00"]
    assert day_slot_times(WEEK_WITH_SHIFT, 0, 60, on_call=False) == ["08:00", "09:00", "10:00", "11:00"]
    assert day_slot_times(WEEK_WITH_SHIFT, 0, 60, on_call=True) == ["19:00", "20:00", "21:00"]
    assert day_slot_times(WEEK_WITH_SHIFT, 5, 60, on_call=False) == []
    # Ranges without the attribute are regular ones.
    assert is_on_call_time(WEEK_WITH_SHIFT, datetime(2026, 10, 26, 20, 0), 60)
    assert not is_on_call_time(WEEK_WITH_SHIFT, datetime(2026, 10, 26, 9, 0), 60)
    assert is_on_call_time(WEEK_WITH_SHIFT, datetime(2026, 10, 31, 9, 0), 60)  # Saturday shift


def test_build_day_slots_flags_on_call_slots():
    day = build_day_slots(
        date(2026, 10, 26), working_hours=WEEK_WITH_SHIFT, time_off=[], slot_minutes=60,
        appointments=[Appt(1, 1, 1, marked_at=datetime(2026, 10, 26, 20, 0))],
    )
    by_time = {slot.time: slot for slot in day.slots}
    assert not by_time["08:00"].on_call
    assert by_time["19:00"].on_call and by_time["19:00"].status == "available"
    assert by_time["20:00"].on_call and by_time["20:00"].status == "booked"


# --- urgent visits ---------------------------------------------------------------


def test_urgent_visits_need_the_offer_and_avoid_on_call_shifts():
    from app.scheduling import UrgentDuringOnCall, UrgentNotOffered, assert_urgent_allowed

    with pytest.raises(UrgentNotOffered):
        assert_urgent_allowed(False, WEEK_WITH_SHIFT, [], 60, datetime(2026, 10, 26, 15, 0))
    assert UrgentNotOffered("x").status_code == 400
    with pytest.raises(UrgentDuringOnCall, match="on call on Monday at 20:00"):
        assert_urgent_allowed(True, WEEK_WITH_SHIFT, [], 60, datetime(2026, 10, 26, 20, 0))
    assert UrgentDuringOnCall("x").status_code == 409
    # Outside the shift: any time, inside or outside the working hours.
    assert_urgent_allowed(True, WEEK_WITH_SHIFT, [], 60, datetime(2026, 10, 26, 9, 0))
    assert_urgent_allowed(True, WEEK_WITH_SHIFT, [], 60, datetime(2026, 10, 26, 15, 17))
    assert_urgent_allowed(True, WEEK_WITH_SHIFT, [], 60, datetime(2026, 10, 25, 3, 0))  # Sunday
    # Time off still holds.
    with pytest.raises(ProfessionalUnavailable):
        assert_urgent_allowed(True, WEEK_WITH_SHIFT, [Off(date(2026, 10, 26))], 60, datetime(2026, 10, 26, 15, 0))


def test_build_day_slots_lists_urgent_visits_outside_the_grid():
    @dataclass
    class Urgent(Appt):
        urgent: bool = True

    day = build_day_slots(
        date(2026, 10, 26), working_hours=MORNING, time_off=[], slot_minutes=60,
        appointments=[Urgent(1, 1, 1, marked_at=datetime(2026, 10, 26, 21, 15)), Urgent(2, 1, 2, marked_at=datetime(2026, 10, 26, 9, 0))],
    )
    times = [(slot.time, slot.status, slot.urgent) for slot in day.slots]
    assert times[:2] == [("08:00", "available", False), ("09:00", "booked", True)]
    assert times[-1] == ("21:15", "booked", True)

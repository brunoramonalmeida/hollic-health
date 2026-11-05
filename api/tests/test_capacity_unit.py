"""Unit tests for the service capacity rules in app.capacity (no HTTP, no database)."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from app.capacity import (
    ClinicFull,
    Load,
    ProfessionalFull,
    assert_capacity,
    count_occupying,
    is_full,
    load_of,
    month_window,
)


@dataclass
class Limited:
    name: str = "Lorem Ipsum"
    daily_limit: int = 0
    monthly_limit: int = 0


@dataclass
class Appt:
    id: int
    marked_at: datetime
    status: str = "agendada"


DAY = date(2026, 10, 26)


def test_month_window():
    assert month_window(date(2026, 10, 26)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert month_window(date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_window(date(2024, 2, 1)) == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_window(date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_count_occupying_ignores_cancelled_and_the_edited_appointment():
    rows = [
        Appt(1, datetime(2026, 10, 26, 8)),
        Appt(2, datetime(2026, 10, 26, 9), status="realizada"),
        Appt(3, datetime(2026, 10, 26, 10), status="cancelada"),
        Appt(4, datetime(2026, 10, 27, 8)),  # other day
    ]
    assert count_occupying(rows, DAY, DAY) == 2
    assert count_occupying(rows, DAY, DAY, exclude_id=1) == 1
    assert count_occupying(rows, date(2026, 10, 1), date(2026, 10, 31)) == 3


def test_load_of_counts_the_day_and_the_calendar_month():
    rows = [Appt(1, datetime(2026, 10, 26, 8)), Appt(2, datetime(2026, 10, 2, 8)), Appt(3, datetime(2026, 11, 2, 8))]
    load = load_of(rows, DAY)
    assert (load.day_count, load.month_count) == (1, 2)


def test_is_full():
    unlimited = Limited()
    assert not is_full(unlimited, Load(DAY, day_count=99, month_count=999))
    daily = Limited(daily_limit=2)
    assert not is_full(daily, Load(DAY, 1, 10))
    assert is_full(daily, Load(DAY, 2, 10))
    monthly = Limited(monthly_limit=10)
    assert not is_full(monthly, Load(DAY, 5, 9))
    assert is_full(monthly, Load(DAY, 0, 10))


def test_assert_capacity_messages():
    with pytest.raises(ProfessionalFull, match=r"fully booked on 2026-10-26: 2 of 2 appointments per day"):
        assert_capacity("professional", Limited(daily_limit=2), Load(DAY, 2, 5))
    with pytest.raises(ClinicFull, match=r"fully booked for 2026-10: 40 of 40 appointments per month"):
        assert_capacity("clinic", Limited(name="Clinic Foo", monthly_limit=40), Load(DAY, 1, 40))
    # Under the limits: nothing raised.
    assert_capacity("professional", Limited(daily_limit=2, monthly_limit=40), Load(DAY, 1, 39))
    assert_capacity("clinic", Limited(), Load(DAY, 99, 999))

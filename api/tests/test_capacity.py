"""API tests for the service capacity of professionals and clinics: once the
daily or monthly limit is reached, scheduling is refused (409) and the
agenda marks the day as full."""

from datetime import datetime

import pytest

from conftest import activate_plan, auth, sign

# 2026-10-26 (Mon) .. 2026-10-30 (Fri) are inside the default working week.
MON = datetime(2026, 10, 26, 8, 0)
TUE = datetime(2026, 10, 27, 8, 0)
WED = datetime(2026, 10, 28, 8, 0)
NEXT_MONTH = datetime(2026, 11, 2, 8, 0)  # Monday of November

ALL_DAY = [{"weekday": w, "start": "08:00", "end": "18:00"} for w in range(5)]


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers

    def clinic(name="Clinic Bar", **limits):
        created = client.post("/clinics", headers=headers, json={"name": name, "city": "X", "fee_cents": 2000, **limits})
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def professional(name="Lorem Ipsum", clinic_id=1, **extra):
        created = client.post(
            "/professionals",
            headers=headers,
            json={"name": name, "specialty_id": 1, "clinic_id": clinic_id, "price_cents": 5000, "working_hours": ALL_DAY, **extra},
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def customer(name="Foo Holder"):
        created = client.post("/customers", headers=headers, json={"name": name})
        activate_plan(client, headers, created.json()["id"])
        return created.json()["id"]

    customers = iter(range(100))

    def book(professional_id, when, customer_id=None, **overrides):
        # A fresh customer per booking unless told otherwise: capacity, not
        # patient conflicts, is what these tests exercise.
        if customer_id is None:
            customer_id = customer(f"Holder {next(customers)}")
        payload = {"customer_id": customer_id, "professional_id": professional_id, "marked_at": when.isoformat()}
        payload.update(overrides)
        return client.post("/appointments", headers=headers, json=payload)

    def edit(item, **overrides):
        payload = {
            "customer_id": item["customer_id"],
            "dependent_id": item["dependent_id"],
            "professional_id": item["professional_id"],
            "marked_at": item["marked_at"],
            "status": item["status"],
            "notes": item["notes"],
        }
        payload.update(overrides)
        if isinstance(payload["marked_at"], datetime):
            payload["marked_at"] = payload["marked_at"].isoformat()
        return client.put(f"/appointments/{item['id']}", headers=headers, json=payload)

    c.clinic, c.professional, c.customer, c.book, c.edit = clinic, professional, customer, book, edit
    return c


# --- professional record ------------------------------------------------------------


def test_professional_and_clinic_carry_their_capacity(ctx):
    clinic = ctx.clinic(daily_limit=10, monthly_limit=200)
    pro = ctx.professional(clinic_id=clinic, daily_limit=4, monthly_limit=60)
    body = ctx.client.get(f"/professionals/{pro}", headers=ctx.headers).json()
    assert (body["daily_limit"], body["monthly_limit"]) == (4, 60)
    assert (body["clinic_daily_limit"], body["clinic_monthly_limit"]) == (10, 200)
    listed = ctx.client.get(f"/clinics/{clinic}", headers=ctx.headers).json()
    assert (listed["daily_limit"], listed["monthly_limit"]) == (10, 200)


def test_capacity_validation(ctx):
    refused = ctx.client.post(
        "/professionals",
        headers=ctx.headers,
        json={"name": "Bad", "specialty_id": 1, "clinic_id": 1, "daily_limit": -1},
    )
    assert refused.status_code == 422
    assert ctx.client.post("/clinics", headers=ctx.headers, json={"name": "Bad", "city": "X", "monthly_limit": -1}).status_code == 422


# --- professional limits -------------------------------------------------------------


def test_professional_daily_limit_blocks_the_day(ctx):
    pro = ctx.professional(daily_limit=2)
    assert ctx.book(pro, MON).status_code == 201
    assert ctx.book(pro, MON.replace(hour=9)).status_code == 201
    refused = ctx.book(pro, MON.replace(hour=10))
    assert refused.status_code == 409
    assert "Professional Lorem Ipsum is fully booked on 2026-10-26" in refused.json()["detail"]
    assert "2 of 2 appointments per day" in refused.json()["detail"]
    # Another day is free.
    assert ctx.book(pro, TUE).status_code == 201


def test_professional_monthly_limit_blocks_the_month(ctx):
    pro = ctx.professional(monthly_limit=2)
    assert ctx.book(pro, MON).status_code == 201
    assert ctx.book(pro, TUE).status_code == 201
    refused = ctx.book(pro, WED)
    assert refused.status_code == 409
    assert "fully booked for 2026-10" in refused.json()["detail"]
    # The next calendar month has its own capacity.
    assert ctx.book(pro, NEXT_MONTH).status_code == 201


def test_zero_means_unlimited(ctx):
    pro = ctx.professional()  # no limits
    for hour in range(8, 13):
        assert ctx.book(pro, MON.replace(hour=hour)).status_code == 201


def test_cancelled_appointments_free_their_place(ctx):
    pro = ctx.professional(daily_limit=1)
    first = ctx.book(pro, MON).json()
    assert ctx.book(pro, MON.replace(hour=9)).status_code == 409
    assert ctx.edit(first, status="cancelada").status_code == 200
    assert ctx.book(pro, MON.replace(hour=9)).status_code == 201


def test_completed_appointments_take_capacity(ctx):
    pro = ctx.professional(daily_limit=1)
    booked = ctx.book(pro, MON).json()
    sign(ctx.client, booked["id"])
    assert ctx.book(pro, MON.replace(hour=9)).status_code == 409


# --- clinic limits --------------------------------------------------------------------


def test_clinic_daily_limit_is_shared_by_its_professionals(ctx):
    clinic = ctx.clinic(daily_limit=2)
    pro_a = ctx.professional("Lorem Ipsum", clinic_id=clinic)
    pro_b = ctx.professional("Dolor Sit", clinic_id=clinic)
    assert ctx.book(pro_a, MON).status_code == 201
    assert ctx.book(pro_b, MON.replace(hour=9)).status_code == 201
    refused = ctx.book(pro_a, MON.replace(hour=10))
    assert refused.status_code == 409
    assert "Clinic Clinic Bar is fully booked on 2026-10-26" in refused.json()["detail"]
    # Another clinic is not affected.
    elsewhere = ctx.professional("Sit Amet", clinic_id=ctx.clinic("Clinic Baz"))
    assert ctx.book(elsewhere, MON.replace(hour=10)).status_code == 201


def test_clinic_monthly_limit(ctx):
    clinic = ctx.clinic(monthly_limit=2)
    pro = ctx.professional(clinic_id=clinic)
    assert ctx.book(pro, MON).status_code == 201
    assert ctx.book(pro, TUE).status_code == 201
    refused = ctx.book(pro, WED)
    assert refused.status_code == 409
    assert "fully booked for 2026-10" in refused.json()["detail"]
    assert ctx.book(pro, NEXT_MONTH).status_code == 201


def test_professional_limit_is_checked_before_the_clinic_one(ctx):
    clinic = ctx.clinic(daily_limit=5)
    pro = ctx.professional(clinic_id=clinic, daily_limit=1)
    assert ctx.book(pro, MON).status_code == 201
    refused = ctx.book(pro, MON.replace(hour=9))
    assert "Professional" in refused.json()["detail"]


# --- editing -----------------------------------------------------------------------


def test_keeping_the_day_never_re_checks(ctx):
    pro = ctx.professional(daily_limit=2)
    first = ctx.book(pro, MON).json()
    ctx.book(pro, MON.replace(hour=9))
    # The professional lowers its capacity below what is already booked...
    updated = ctx.client.put(
        f"/professionals/{pro}",
        headers=ctx.headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "daily_limit": 1},
    )
    assert updated.status_code == 200, updated.text
    # ...existing appointments stand: notes, time within the day, completion.
    assert ctx.edit(first, notes="follow-up").status_code == 200
    assert ctx.edit(first, marked_at=MON.replace(hour=10)).status_code == 200
    # Only new bookings are refused.
    assert ctx.book(pro, MON.replace(hour=11)).status_code == 409


def test_moving_to_a_full_day_is_refused(ctx):
    pro = ctx.professional(daily_limit=1)
    ctx.book(pro, MON)
    other = ctx.book(pro, TUE).json()
    refused = ctx.edit(other, marked_at=MON.replace(hour=9))
    assert refused.status_code == 409
    assert "fully booked on 2026-10-26" in refused.json()["detail"]


def test_moving_within_the_month_does_not_count_itself(ctx):
    pro = ctx.professional(monthly_limit=2)
    ctx.book(pro, MON)
    other = ctx.book(pro, TUE).json()
    # The month is at capacity, but the appointment moving inside it keeps its own place.
    assert ctx.edit(other, marked_at=WED).status_code == 200


def test_moving_to_a_full_professional_is_refused(ctx):
    busy = ctx.professional("Busy One", daily_limit=1)
    free = ctx.professional("Free One")
    ctx.book(busy, MON)
    item = ctx.book(free, MON.replace(hour=9)).json()
    refused = ctx.edit(item, professional_id=busy, marked_at=MON.replace(hour=10))
    assert refused.status_code == 409
    assert "Busy One" in refused.json()["detail"]


def test_reactivating_needs_capacity(ctx):
    pro = ctx.professional(daily_limit=1)
    first = ctx.book(pro, MON).json()
    cancelled = ctx.edit(first, status="cancelada").json()
    ctx.book(pro, MON.replace(hour=9))  # takes the freed place
    refused = ctx.edit(cancelled, status="agendada")
    assert refused.status_code == 409
    # And cancelling is always possible, even over capacity.
    second = ctx.client.get("/appointments", headers=ctx.headers).json()[0]
    assert ctx.edit(second, status="cancelada").status_code == 200


# --- agenda ------------------------------------------------------------------------


def test_slots_mark_days_at_capacity_as_full(ctx):
    pro = ctx.professional(daily_limit=1)
    booked = ctx.book(pro, MON).json()
    days = {d["date"]: d for d in ctx.client.get(f"/professionals/{pro}/slots?start=2026-10-26&end=2026-10-27", headers=ctx.headers).json()}
    monday, tuesday = days["2026-10-26"], days["2026-10-27"]
    assert monday["full"] is True and monday["full_reason"] == "professional"
    statuses = {s["time"]: s["status"] for s in monday["slots"]}
    assert statuses["08:00"] == "booked"  # the booked one still shows who is there
    assert statuses["09:00"] == "full"
    assert tuesday["full"] is False
    assert {s["status"] for s in tuesday["slots"]} == {"available"}
    # Cancelling reopens the day.
    ctx.edit(booked, status="cancelada")
    days = ctx.client.get(f"/professionals/{pro}/slots?start=2026-10-26&end=2026-10-26", headers=ctx.headers).json()
    assert days[0]["full"] is False


def test_slots_mark_clinic_capacity_too(ctx):
    clinic = ctx.clinic(monthly_limit=1)
    pro_a = ctx.professional("Lorem Ipsum", clinic_id=clinic)
    pro_b = ctx.professional("Dolor Sit", clinic_id=clinic)
    ctx.book(pro_a, MON)
    days = ctx.client.get(f"/professionals/{pro_b}/slots?start=2026-10-27&end=2026-10-27", headers=ctx.headers).json()
    assert days[0]["full"] is True and days[0]["full_reason"] == "clinic"
    # November is outside the full month.
    days = ctx.client.get(f"/professionals/{pro_b}/slots?start=2026-11-02&end=2026-11-02", headers=ctx.headers).json()
    assert days[0]["full"] is False


def test_time_off_stays_blocked_not_full(ctx):
    pro = ctx.professional(daily_limit=1)
    ctx.client.post(f"/professionals/{pro}/time-off", headers=ctx.headers, json={"date": "2026-10-26", "start": "09:00", "end": "10:00"})
    ctx.book(pro, MON)
    days = ctx.client.get(f"/professionals/{pro}/slots?start=2026-10-26&end=2026-10-26", headers=ctx.headers).json()
    statuses = {s["time"]: s["status"] for s in days[0]["slots"]}
    assert statuses["09:00"] == "blocked"
    assert statuses["10:00"] == "full"


def test_slots_can_exclude_the_appointment_being_moved(ctx):
    pro = ctx.professional(daily_limit=1)
    booked = ctx.book(pro, MON).json()
    # Booking view: Monday is full...
    days = ctx.client.get(f"/professionals/{pro}/slots?start=2026-10-26&end=2026-10-26", headers=ctx.headers).json()
    assert days[0]["full"] is True
    # ...editing view: the appointment being moved keeps its own place.
    days = ctx.client.get(
        f"/professionals/{pro}/slots?start=2026-10-26&end=2026-10-26&exclude_appointment={booked['id']}", headers=ctx.headers
    ).json()
    assert days[0]["full"] is False
    statuses = {s["time"]: s["status"] for s in days[0]["slots"]}
    assert statuses["08:00"] == "booked"
    assert statuses["09:00"] == "available"

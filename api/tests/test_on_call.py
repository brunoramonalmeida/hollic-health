"""API tests for on-call services: professionals available for on-call have
on-call shifts in their week, and appointments booked in those slots use the
on-call price list (the professional's on-call rate above its regular fee,
the clinic's and the specialty's on-call values)."""

from datetime import datetime

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign

MON_9 = datetime(2026, 10, 26, 9, 0)  # regular hours
MON_20 = datetime(2026, 10, 26, 20, 0)  # Monday evening on-call shift
SAT_9 = datetime(2026, 10, 31, 9, 0)  # Saturday on-call shift
REGULAR = [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]
ON_CALL = [{"weekday": 0, "start": "19:00", "end": "22:00"}, {"weekday": 5, "start": "08:00", "end": "12:00"}]
# Test catalog: clinic fee 2000, specialty 8000; professional 5000 regular / 9000 on-call.
REGULAR_GROSS = 2000 + 8000 + 5000


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers, discount_percent=20)

    def professional(name="Lorem Ipsum", **extra):
        payload = {
            "name": name,
            "specialty_id": 1,
            "clinic_id": 1,
            "price_cents": 5000,
            "on_call_enabled": True,
            "on_call_price_cents": 9000,
            "working_hours": REGULAR,
            "on_call_hours": ON_CALL,
        }
        payload.update(extra)
        return client.post("/professionals", headers=headers, json=payload)

    created = professional()
    assert created.status_code == 201, created.text
    c.professional = created.json()
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)

    def book(when, **overrides):
        payload = {"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": when.isoformat()}
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

    def update_professional(**changes):
        payload = {
            "name": "Lorem Ipsum",
            "specialty_id": 1,
            "clinic_id": 1,
            "price_cents": 5000,
            "on_call_enabled": True,
            "on_call_price_cents": 9000,
        }
        payload.update(changes)
        return client.put(f"/professionals/{c.professional['id']}", headers=headers, json=payload)

    def quote(when):
        response = client.get(
            f"/appointments/quote?customer_id={c.holder}&professional_id={c.professional['id']}&at={when.isoformat()}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    c.new_professional, c.book, c.edit, c.update_professional, c.quote = professional, book, edit, update_professional, quote
    return c


# --- professional register -------------------------------------------------------------


def test_professional_carries_on_call_availability_rate_and_shifts(ctx):
    body = ctx.professional
    assert body["on_call_enabled"] is True
    assert body["on_call_price_cents"] == 9000
    assert body["on_call_hours"] == [
        {"weekday": 0, "start": "19:00", "end": "22:00"},
        {"weekday": 5, "start": "08:00", "end": "12:00"},
    ]
    assert body["working_hours"] == REGULAR  # the shifts are not mixed with the regular hours
    assert body["clinic_on_call_fee_cents"] is None
    assert body["specialty_on_call_price_cents"] is None
    plain = ctx.new_professional("Dolor Sit", on_call_enabled=False, on_call_price_cents=0, on_call_hours=None).json()
    assert (plain["on_call_enabled"], plain["on_call_hours"]) == (False, [])


def test_on_call_rate_must_be_greater_than_the_regular_fee(ctx):
    assert ctx.new_professional("Cheap", on_call_price_cents=5000).status_code == 422
    assert ctx.new_professional("Cheaper", on_call_price_cents=4000).status_code == 422
    assert ctx.update_professional(on_call_price_cents=5000).status_code == 422
    # Without on-call availability the rate is not checked.
    assert ctx.new_professional("Off", on_call_enabled=False, on_call_price_cents=0, on_call_hours=None).status_code == 201


def test_on_call_shifts_require_availability(ctx):
    assert ctx.new_professional("Off", on_call_enabled=False, on_call_price_cents=0).status_code == 422
    put = ctx.client.put(
        f"/professionals/{ctx.professional['id']}/working-hours",
        headers=ctx.headers,
        json={"ranges": REGULAR, "on_call_ranges": ON_CALL},
    )
    assert put.status_code == 200, put.text
    # Disabling on-call removes the shifts; a later PUT of shifts is refused.
    off = ctx.update_professional(on_call_enabled=False, on_call_price_cents=0)
    assert off.status_code == 200, off.text
    assert off.json()["on_call_hours"] == []
    put = ctx.client.put(
        f"/professionals/{ctx.professional['id']}/working-hours",
        headers=ctx.headers,
        json={"ranges": REGULAR, "on_call_ranges": ON_CALL},
    )
    assert put.status_code == 400


def test_shifts_cannot_overlap_the_working_hours(ctx):
    clash = ctx.new_professional("Clash", on_call_hours=[{"weekday": 0, "start": "11:00", "end": "14:00"}])
    assert clash.status_code == 400
    assert "overlaps the working hours" in clash.json()["detail"]
    # Shifts cannot overlap each other either.
    double = ctx.new_professional(
        "Double", on_call_hours=[{"weekday": 5, "start": "08:00", "end": "12:00"}, {"weekday": 5, "start": "11:00", "end": "13:00"}]
    )
    assert double.status_code == 400
    assert "On-call shifts overlap" in double.json()["detail"]
    # On update, the new regular hours are checked against the current shifts.
    moved = ctx.update_professional(working_hours=[{"weekday": 5, "start": "10:00", "end": "16:00"}])
    assert moved.status_code == 400
    # And unchanged shifts are kept when the body does not mention them.
    kept = ctx.update_professional(working_hours=REGULAR).json()
    assert len(kept["on_call_hours"]) == 2


def test_clinic_and_specialty_on_call_values_are_optional_and_never_below_regular(ctx):
    clinic = ctx.client.put("/clinics/1", headers=ctx.headers, json={"name": "Clinic Foo", "city": "Sample", "fee_cents": 2000, "on_call_fee_cents": 3000})
    assert clinic.status_code == 200, clinic.text
    assert clinic.json()["on_call_fee_cents"] == 3000
    assert ctx.client.put("/clinics/1", headers=ctx.headers, json={"name": "Clinic Foo", "city": "Sample", "fee_cents": 2000, "on_call_fee_cents": 1500}).status_code == 422
    specialty = ctx.client.put("/specialties/1", headers=ctx.headers, json={"name": "Clínico geral", "price_cents": 8000, "on_call_price_cents": 10000})
    assert specialty.status_code == 200, specialty.text
    assert specialty.json()["on_call_price_cents"] == 10000
    assert ctx.client.put("/specialties/1", headers=ctx.headers, json={"name": "Clínico geral", "price_cents": 8000, "on_call_price_cents": 7000}).status_code == 422
    # Back to "same as regular".
    cleared = ctx.client.put("/specialties/1", headers=ctx.headers, json={"name": "Clínico geral", "price_cents": 8000, "on_call_price_cents": None}).json()
    assert cleared["on_call_price_cents"] is None
    body = ctx.client.get(f"/professionals/{ctx.professional['id']}", headers=ctx.headers).json()
    assert body["clinic_on_call_fee_cents"] == 3000


# --- agenda ------------------------------------------------------------------------------


def test_slots_flag_the_on_call_shifts(ctx):
    days = {d["date"]: d for d in ctx.client.get(f"/professionals/{ctx.professional['id']}/slots?start=2026-10-26&end=2026-11-01", headers=ctx.headers).json()}
    monday = {s["time"]: s for s in days["2026-10-26"]["slots"]}
    assert monday["09:00"]["on_call"] is False
    assert monday["19:00"]["on_call"] is True and monday["19:00"]["status"] == "available"
    saturday = days["2026-10-31"]["slots"]
    assert saturday and all(s["on_call"] for s in saturday)
    assert days["2026-11-01"]["slots"] == []  # Sunday: nothing


def test_shifts_of_a_professional_not_available_are_not_offered(ctx):
    ctx.update_professional(on_call_enabled=False, on_call_price_cents=0)
    days = {d["date"]: d for d in ctx.client.get(f"/professionals/{ctx.professional['id']}/slots?start=2026-10-26&end=2026-10-31", headers=ctx.headers).json()}
    assert days["2026-10-31"]["slots"] == []
    assert ctx.book(SAT_9).status_code == 400


# --- pricing --------------------------------------------------------------------------


def test_on_call_appointment_uses_the_on_call_price_list(ctx):
    regular = ctx.book(MON_9).json()
    assert regular["on_call"] is False
    assert (regular["fee_cents"], regular["specialty_price_cents"], regular["professional_price_cents"]) == (2000, 8000, 5000)
    assert regular["amount_cents"] == 12000
    shift = ctx.book(MON_20).json()
    assert shift["on_call"] is True
    # Clinic and specialty have no on-call value: regular ones; the professional's on-call rate applies.
    assert (shift["fee_cents"], shift["specialty_price_cents"], shift["professional_price_cents"]) == (2000, 8000, 9000)
    assert shift["gross_cents"] == 19000
    assert shift["discount_percent"] == 20  # the plan discount applies as usual
    assert shift["amount_cents"] == 15200


def test_clinic_and_specialty_on_call_values_enter_the_price(ctx):
    ctx.client.put("/clinics/1", headers=ctx.headers, json={"name": "Clinic Foo", "city": "Sample", "fee_cents": 2000, "on_call_fee_cents": 3000})
    ctx.client.put("/specialties/1", headers=ctx.headers, json={"name": "Clínico geral", "price_cents": 8000, "on_call_price_cents": 10000})
    shift = ctx.book(SAT_9).json()
    assert (shift["fee_cents"], shift["specialty_price_cents"], shift["professional_price_cents"]) == (3000, 10000, 9000)
    assert shift["gross_cents"] == 22000
    # A regular slot still uses the regular list.
    assert ctx.book(MON_9).json()["gross_cents"] == REGULAR_GROSS


def test_rates_change_dynamically_for_new_bookings_only(ctx):
    before = ctx.book(SAT_9).json()
    assert before["professional_price_cents"] == 9000
    assert ctx.update_professional(on_call_price_cents=12000).status_code == 200
    # Snapshot: the booked one keeps its rate...
    assert ctx.client.get(f"/appointments/{before['id']}", headers=ctx.headers).json()["professional_price_cents"] == 9000
    assert ctx.edit(before, notes="x").json()["professional_price_cents"] == 9000
    # ...the next one uses the new rate.
    assert ctx.book(MON_20).json()["professional_price_cents"] == 12000


def test_moving_between_regular_and_on_call_slots_reprices(ctx):
    item = ctx.book(MON_9).json()
    moved = ctx.edit(item, marked_at=MON_20)
    assert moved.status_code == 200, moved.text
    assert moved.json()["on_call"] is True
    assert moved.json()["professional_price_cents"] == 9000
    assert moved.json()["gross_cents"] == 19000
    back = ctx.edit(moved.json(), marked_at=MON_9)
    assert back.json()["on_call"] is False
    assert back.json()["gross_cents"] == REGULAR_GROSS


def test_moving_within_the_same_kind_keeps_the_snapshot(ctx):
    item = ctx.book(MON_20).json()
    ctx.update_professional(on_call_price_cents=12000)
    moved = ctx.edit(item, marked_at=MON_20.replace(hour=21)).json()
    assert moved["on_call"] is True
    assert moved["professional_price_cents"] == 9000  # price list snapshot kept


def test_quote_previews_the_on_call_price(ctx):
    regular = ctx.quote(MON_9)
    assert regular["on_call"] is False and regular["gross_cents"] == REGULAR_GROSS
    shift = ctx.quote(SAT_9)
    assert shift["on_call"] is True
    assert shift["professional_price_cents"] == 9000
    assert shift["gross_cents"] == 19000
    assert shift["amount_cents"] == 15200
    # `at` also sets the date of the plan allowance period.
    assert shift["plan_window_start"] <= "2026-10-31" <= shift["plan_window_end"]


# --- payout, reports, notifications -------------------------------------------------------


def test_professional_is_paid_the_on_call_rate(ctx):
    shift = ctx.book(SAT_9).json()
    regular = ctx.book(MON_9).json()
    sign(ctx.client, shift["id"])
    sign(ctx.client, regular["id"])
    statement = ctx.client.get(
        f"/reports/payouts/{ctx.professional['id']}?start=2026-10-26&end=2026-10-31", headers=ctx.headers
    ).json()
    assert statement["payout_cents"] == 9000 + 5000
    assert (statement["on_call"], statement["on_call_cents"]) == (1, 9000)
    assert statement["on_call_fee_cents"] == 9000
    lines = {line["appointment_id"]: line for line in statement["lines"]}
    assert lines[shift["id"]]["on_call"] is True and lines[shift["id"]]["payout_cents"] == 9000
    assert lines[regular["id"]]["on_call"] is False and lines[regular["id"]]["payout_cents"] == 5000
    report = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.headers).json()
    (row,) = report["professionals"]
    assert (row["on_call"], row["on_call_cents"], row["on_call_fee_cents"]) == (1, 9000, 9000)
    csv = ctx.client.get("/reports/payouts.csv?start=2026-10-26&end=2026-10-31", headers=ctx.headers).text
    header, *rows = csv.strip().splitlines()
    column = header.split(";").index("on_call")
    assert sorted(line.split(";")[column] for line in rows) == ["no", "yes"]


def test_appointments_report_counts_on_call_services(ctx):
    ctx.book(SAT_9)
    ctx.book(MON_9)
    ctx.book(MON_20, status="cancelada")  # cancelled: counted as cancelled, adds no money
    body = ctx.client.get("/reports/appointments?granularity=week&start=2026-10-26&end=2026-11-01", headers=ctx.headers).json()
    assert body["totals"]["on_call"] == 1
    assert body["totals"]["on_call_cents"] == 15200
    assert body["periods"][0]["on_call"] == 1
    assert body["by_professional"][0]["on_call"] == 1


def test_on_call_booking_tells_the_customer(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    ctx.book(SAT_9)
    bodies = [item["body"] for item in ctx.client.get("/notifications", headers=client_headers).json()]
    assert any("On-call service: yes" in body for body in bodies), bodies


def test_professional_login_sees_its_on_call_fee(ctx):
    shift = ctx.book(SAT_9).json()
    headers = auth_as(ctx.client, "professional", professional_id=ctx.professional["id"])
    own = ctx.client.get(f"/appointments/{shift['id']}", headers=headers).json()
    assert own["on_call"] is True
    assert own["professional_price_cents"] == 9000

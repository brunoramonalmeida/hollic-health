"""API tests for ad-hoc urgent visits: professionals flag the availability
in their own profile, the rate is at least three times the regular fee,
urgent visits are booked at any time except during on-call shifts, and they
reach the payout and the reports."""

from datetime import datetime

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign

MON_9 = datetime(2026, 10, 26, 9, 0)  # regular hours
MON_20 = datetime(2026, 10, 26, 20, 0)  # Monday evening on-call shift
WED_NIGHT = datetime(2026, 10, 28, 21, 15)  # outside every range, off the grid
SUN_3 = datetime(2026, 10, 25, 3, 0)  # Sunday small hours
REGULAR = [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]
ON_CALL = [{"weekday": 0, "start": "19:00", "end": "22:00"}]
# Test catalog: clinic fee 2000, specialty 8000; professional 5000 regular / 9000 on-call / 15000 urgent.
FEE, URGENT_RATE = 5000, 15000


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
            "price_cents": FEE,
            "on_call_enabled": True,
            "on_call_price_cents": 9000,
            "urgent_enabled": True,
            "urgent_price_cents": URGENT_RATE,
            "working_hours": REGULAR,
            "on_call_hours": ON_CALL,
        }
        payload.update(extra)
        return client.post("/professionals", headers=headers, json=payload)

    created = professional()
    assert created.status_code == 201, created.text
    c.professional = created.json()
    c.login = auth_as(client, "professional", professional_id=c.professional["id"])
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)

    def book(when, urgent=True, **overrides):
        payload = {"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": when.isoformat(), "urgent": urgent}
        payload.update(overrides)
        return client.post("/appointments", headers=headers, json=payload)

    def edit(item, **overrides):
        payload = {key: item[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "status", "notes", "urgent")}
        payload.update(overrides)
        if isinstance(payload["marked_at"], datetime):
            payload["marked_at"] = payload["marked_at"].isoformat()
        return client.put(f"/appointments/{item['id']}", headers=headers, json=payload)

    def set_urgent(**body):
        return client.put("/professionals/me/urgent", headers=c.login, json=body)

    def quote(when, urgent=True):
        return client.get(
            f"/appointments/quote?customer_id={c.holder}&professional_id={c.professional['id']}&at={when.isoformat()}&urgent={'true' if urgent else 'false'}",
            headers=headers,
        )

    c.new_professional, c.book, c.edit, c.set_urgent, c.quote = professional, book, edit, set_urgent, quote
    return c


# --- the professional's own profile ----------------------------------------------------


def test_professional_reads_its_own_profile(ctx):
    me = ctx.client.get("/professionals/me", headers=ctx.login).json()
    assert me["id"] == ctx.professional["id"]
    assert me["urgent_enabled"] is True
    assert me["urgent_price_cents"] == URGENT_RATE
    assert me["urgent_minimum_cents"] == 3 * FEE
    # Only professional logins have a profile.
    assert ctx.client.get("/professionals/me", headers=ctx.headers).status_code == 403


def test_professional_flags_urgent_availability_in_its_profile(ctx):
    off = ctx.set_urgent(urgent_enabled=False)
    assert off.status_code == 200, off.text
    assert off.json()["urgent_enabled"] is False
    assert ctx.book(WED_NIGHT).status_code == 400  # not offered any more
    on = ctx.set_urgent(urgent_enabled=True, urgent_price_cents=16000)
    assert on.status_code == 200, on.text
    assert on.json()["urgent_price_cents"] == 16000
    assert ctx.book(WED_NIGHT).json()["professional_price_cents"] == 16000


def test_urgent_rate_is_at_least_three_times_the_fee(ctx):
    refused = ctx.set_urgent(urgent_enabled=True, urgent_price_cents=14999)
    assert refused.status_code == 400
    assert "three times" in refused.json()["detail"]
    assert ctx.set_urgent(urgent_enabled=True, urgent_price_cents=15000).status_code == 200
    # Admin side: the same rule on the professional form.
    assert ctx.new_professional("Cheap", urgent_price_cents=14999).status_code == 422
    assert ctx.new_professional("Fair", urgent_price_cents=15000).status_code == 201
    # Not offered: the rate is not checked.
    assert ctx.new_professional("Off", urgent_enabled=False, urgent_price_cents=0).status_code == 201


def test_enabling_without_a_rate_uses_the_minimum(ctx):
    fresh = ctx.new_professional("Fresh", urgent_enabled=False, urgent_price_cents=0).json()
    login = auth_as(ctx.client, "professional", professional_id=fresh["id"])
    enabled = ctx.client.put("/professionals/me/urgent", headers=login, json={"urgent_enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["urgent_price_cents"] == 3 * FEE
    # A rate above the minimum is kept when enabling again without one.
    ctx.client.put("/professionals/me/urgent", headers=login, json={"urgent_enabled": True, "urgent_price_cents": 20000})
    ctx.client.put("/professionals/me/urgent", headers=login, json={"urgent_enabled": False})
    again = ctx.client.put("/professionals/me/urgent", headers=login, json={"urgent_enabled": True}).json()
    assert again["urgent_price_cents"] == 20000


def test_raising_the_fee_makes_a_low_urgent_rate_invalid(ctx):
    # The admin raises the regular fee: the urgent rate must follow (3x).
    refused = ctx.client.put(
        f"/professionals/{ctx.professional['id']}",
        headers=ctx.headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 6000, "on_call_enabled": True, "on_call_price_cents": 9000, "urgent_enabled": True, "urgent_price_cents": 15000},
    )
    assert refused.status_code == 422


# --- booking ---------------------------------------------------------------------------


def test_urgent_visit_can_be_booked_at_any_time_outside_on_call(ctx):
    night = ctx.book(WED_NIGHT)
    assert night.status_code == 201, night.text
    assert night.json()["urgent"] is True
    assert night.json()["on_call"] is False
    assert night.json()["marked_at"].startswith("2026-10-28T21:15")
    assert ctx.book(SUN_3).status_code == 201  # no working hours on Sunday: still fine
    assert ctx.book(MON_9).status_code == 201  # inside the regular hours too


def test_urgent_visit_is_refused_during_on_call_shifts(ctx):
    refused = ctx.book(MON_20)
    assert refused.status_code == 409
    assert "urgent visits are not accepted during on-call shifts" in refused.json()["detail"]
    # The same slot takes a regular on-call booking.
    assert ctx.book(MON_20, urgent=False).status_code == 201


def test_urgent_visit_needs_a_professional_who_offers_them(ctx):
    plain = ctx.new_professional("Dolor Sit", urgent_enabled=False, urgent_price_cents=0).json()
    refused = ctx.book(WED_NIGHT, professional_id=plain["id"])
    assert refused.status_code == 400
    assert "does not take urgent visits" in refused.json()["detail"]


def test_non_urgent_booking_still_needs_a_working_slot(ctx):
    assert ctx.book(WED_NIGHT, urgent=False).status_code == 400


def test_urgent_visit_respects_time_off_conflicts_and_capacity(ctx):
    ctx.client.post(f"/professionals/{ctx.professional['id']}/time-off", headers=ctx.headers, json={"date": "2026-10-28"})
    assert ctx.book(WED_NIGHT).status_code == 400
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.headers, other, plan=ctx.plan)
    assert ctx.book(SUN_3).status_code == 201
    assert ctx.book(SUN_3, customer_id=other).status_code == 409  # professional busy at that minute
    limited = ctx.new_professional("Busy", daily_limit=1).json()
    assert ctx.book(SUN_3.replace(hour=5), professional_id=limited["id"]).status_code == 201
    refused = ctx.book(SUN_3.replace(hour=6), professional_id=limited["id"], customer_id=other)
    assert refused.status_code == 409 and "fully booked" in refused.json()["detail"]


# --- pricing -----------------------------------------------------------------------------


def test_urgent_visit_pays_the_urgent_rate_with_regular_clinic_and_specialty_values(ctx):
    ctx.client.put("/clinics/1", headers=ctx.headers, json={"name": "Clinic Foo", "city": "Sample", "fee_cents": 2000, "on_call_fee_cents": 3000})
    item = ctx.book(WED_NIGHT).json()
    assert (item["fee_cents"], item["specialty_price_cents"], item["professional_price_cents"]) == (2000, 8000, URGENT_RATE)
    assert item["gross_cents"] == 25000
    assert item["discount_percent"] == 20  # the plan discount applies as usual
    assert item["amount_cents"] == 20000


def test_quote_previews_the_urgent_rate_and_its_rules(ctx):
    quoted = ctx.quote(WED_NIGHT)
    assert quoted.status_code == 200, quoted.text
    assert quoted.json()["urgent"] is True
    assert quoted.json()["professional_price_cents"] == URGENT_RATE
    assert quoted.json()["gross_cents"] == 25000
    assert ctx.quote(MON_20).status_code == 409  # on-call shift
    assert ctx.quote(MON_20, urgent=False).json()["on_call"] is True
    ctx.set_urgent(urgent_enabled=False)
    assert ctx.quote(WED_NIGHT).status_code == 400


def test_switching_urgent_on_or_off_reprices(ctx):
    item = ctx.book(MON_9).json()
    assert item["professional_price_cents"] == URGENT_RATE
    regular = ctx.edit(item, urgent=False)
    assert regular.status_code == 200, regular.text
    assert regular.json()["urgent"] is False
    assert regular.json()["professional_price_cents"] == FEE
    back = ctx.edit(regular.json(), urgent=True).json()
    assert back["professional_price_cents"] == URGENT_RATE


def test_rate_changes_affect_new_visits_only(ctx):
    before = ctx.book(WED_NIGHT).json()
    ctx.set_urgent(urgent_enabled=True, urgent_price_cents=18000)
    assert ctx.edit(before, notes="x").json()["professional_price_cents"] == URGENT_RATE
    assert ctx.book(SUN_3).json()["professional_price_cents"] == 18000


def test_signed_urgent_visit_is_frozen(ctx):
    item = ctx.book(WED_NIGHT).json()
    sign(ctx.client, item["id"])
    assert ctx.edit(item, status="realizada", urgent=False).status_code == 409


# --- agenda, payout, reports, notifications ---------------------------------------------


def test_agenda_shows_urgent_visits_even_outside_the_hours(ctx):
    ctx.book(WED_NIGHT)
    ctx.book(MON_9)
    days = {d["date"]: d for d in ctx.client.get(f"/professionals/{ctx.professional['id']}/slots?start=2026-10-26&end=2026-10-28", headers=ctx.headers).json()}
    wednesday = {s["time"]: s for s in days["2026-10-28"]["slots"]}
    assert wednesday["21:15"]["status"] == "booked" and wednesday["21:15"]["urgent"] is True
    assert wednesday["09:00"]["status"] == "available" and wednesday["09:00"]["urgent"] is False
    monday = {s["time"]: s for s in days["2026-10-26"]["slots"]}
    assert monday["09:00"]["status"] == "booked" and monday["09:00"]["urgent"] is True


def test_professional_is_paid_the_urgent_rate(ctx):
    urgent = ctx.book(WED_NIGHT).json()
    regular = ctx.book(MON_9, urgent=False).json()
    sign(ctx.client, urgent["id"])
    sign(ctx.client, regular["id"])
    statement = ctx.client.get(f"/reports/payouts/{ctx.professional['id']}?start=2026-10-26&end=2026-10-31", headers=ctx.headers).json()
    assert statement["payout_cents"] == URGENT_RATE + FEE
    assert (statement["urgent"], statement["urgent_cents"], statement["urgent_fee_cents"]) == (1, URGENT_RATE, URGENT_RATE)
    lines = {line["appointment_id"]: line for line in statement["lines"]}
    assert lines[urgent["id"]]["urgent"] is True and lines[urgent["id"]]["payout_cents"] == URGENT_RATE
    report = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.headers).json()
    (row,) = report["professionals"]
    assert (row["urgent"], row["urgent_cents"], row["urgent_fee_cents"]) == (1, URGENT_RATE, URGENT_RATE)
    csv = ctx.client.get("/reports/payouts.csv?start=2026-10-26&end=2026-10-31", headers=ctx.headers).text
    header, *rows = csv.strip().splitlines()
    column = header.split(";").index("urgent")
    assert sorted(line.split(";")[column] for line in rows) == ["no", "yes"]
    # The professional sees the same in its own payout.
    own = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.login).json()
    assert own["professionals"][0]["urgent_cents"] == URGENT_RATE


def test_appointments_report_counts_urgent_visits(ctx):
    ctx.book(WED_NIGHT)
    ctx.book(MON_9, urgent=False)
    body = ctx.client.get("/reports/appointments?granularity=week&start=2026-10-26&end=2026-11-01", headers=ctx.headers).json()
    assert body["totals"]["urgent"] == 1
    assert body["totals"]["urgent_cents"] == 20000
    assert body["by_professional"][0]["urgent"] == 1


def test_urgent_booking_tells_the_customer_and_the_professional(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    item = ctx.book(WED_NIGHT).json()
    bodies = [n["body"] for n in ctx.client.get("/notifications", headers=client_headers).json()]
    assert any("Urgent visit: yes" in body for body in bodies), bodies
    own = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.login).json()
    assert own["urgent"] is True and own["professional_price_cents"] == URGENT_RATE


def test_omitting_the_flag_on_edit_keeps_the_kind_of_visit(ctx):
    item = ctx.book(WED_NIGHT).json()
    payload = {key: item[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    cancelled = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["urgent"] is True
    assert cancelled.json()["professional_price_cents"] == URGENT_RATE  # snapshot kept
    back = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**payload, "status": "agendada"})
    assert back.status_code == 200, back.text  # re-activated as an urgent visit (off the grid is fine)
    assert back.json()["urgent"] is True

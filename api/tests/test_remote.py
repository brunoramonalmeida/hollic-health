"""API tests for remote appointments (video call): professionals offer them
in their own profile (or the staff does on the professional form), they are
booked in the regular working hours like in-person ones — never in an
on-call shift, never urgent — priced with the remote price list, carry a
meeting link, and reach the agenda, the notifications, the payout and the
reports. In person stays the default."""

from datetime import datetime, timedelta

import pytest

from app.reminders import schedule_reminders
from conftest import activate_plan, auth, auth_as, ensure_plan, sign

MON_9 = datetime(2026, 10, 26, 9, 0)  # regular hours
MON_10 = datetime(2026, 10, 26, 10, 0)
TUE_9 = datetime(2026, 10, 27, 9, 0)
MON_20 = datetime(2026, 10, 26, 20, 0)  # Monday evening on-call shift
WED_NIGHT = datetime(2026, 10, 28, 21, 15)  # outside every range
REGULAR = [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]
ON_CALL = [{"weekday": 0, "start": "19:00", "end": "22:00"}]
# Test catalog: clinic fee 2000, specialty 8000; professional 5000 regular / 9000 on-call / 15000 urgent / 4000 remote.
FEE, REMOTE_RATE, URGENT_RATE = 5000, 4000, 15000


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
            "remote_enabled": True,
            "remote_price_cents": REMOTE_RATE,
            "working_hours": REGULAR,
            "on_call_hours": ON_CALL,
        }
        payload.update(extra)
        return client.post("/professionals", headers=headers, json=payload)

    created = professional()
    assert created.status_code == 201, created.text
    c.professional = created.json()
    c.login = auth_as(client, "professional", professional_id=c.professional["id"])
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)

    def book(when, remote=True, **overrides):
        payload = {"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": when.isoformat(), "remote": remote}
        payload.update(overrides)
        return client.post("/appointments", headers=headers, json=payload)

    def edit(item, **overrides):
        payload = {key: item[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "status", "notes", "urgent", "remote")}
        payload.update(overrides)
        if isinstance(payload["marked_at"], datetime):
            payload["marked_at"] = payload["marked_at"].isoformat()
        return client.put(f"/appointments/{item['id']}", headers=headers, json=payload)

    def set_remote(**body):
        return client.put("/professionals/me/remote", headers=c.login, json=body)

    def quote(when, remote=True, urgent=False):
        return client.get(
            f"/appointments/quote?customer_id={c.holder}&professional_id={c.professional['id']}&at={when.isoformat()}"
            f"&remote={'true' if remote else 'false'}&urgent={'true' if urgent else 'false'}",
            headers=headers,
        )

    c.new_professional, c.book, c.edit, c.set_remote, c.quote = professional, book, edit, set_remote, quote
    return c


# --- the professional's own profile ----------------------------------------------------


def test_profile_exposes_the_remote_offer_and_rate(ctx):
    me = ctx.client.get("/professionals/me", headers=ctx.login).json()
    assert me["remote_enabled"] is True
    assert me["remote_price_cents"] == REMOTE_RATE
    assert me["remote_fee_cents"] == REMOTE_RATE
    assert me["clinic_remote_fee_cents"] is None and me["specialty_remote_price_cents"] is None


def test_professional_flags_remote_availability_in_its_profile(ctx):
    off = ctx.set_remote(remote_enabled=False)
    assert off.status_code == 200, off.text
    assert off.json()["remote_enabled"] is False
    assert off.json()["remote_price_cents"] == REMOTE_RATE  # the rate is kept for later
    refused = ctx.book(MON_9)
    assert refused.status_code == 400 and "does not offer remote appointments" in refused.json()["detail"]
    on = ctx.set_remote(remote_enabled=True, remote_price_cents=3500)
    assert on.status_code == 200, on.text
    assert on.json()["remote_price_cents"] == 3500
    assert ctx.book(MON_9).json()["professional_price_cents"] == 3500
    # Back to "same as the fee".
    same = ctx.set_remote(remote_enabled=True, same_as_fee=True).json()
    assert same["remote_price_cents"] is None and same["remote_fee_cents"] == FEE
    assert ctx.book(TUE_9).json()["professional_price_cents"] == FEE
    # Only professional logins: the staff edits the professional form instead.
    assert ctx.client.put("/professionals/me/remote", headers=ctx.headers, json={"remote_enabled": True}).status_code == 403


def test_enabling_without_a_rate_pays_the_regular_fee(ctx):
    fresh = ctx.new_professional("Fresh", remote_enabled=False, remote_price_cents=None).json()
    assert fresh["remote_enabled"] is False and fresh["remote_price_cents"] is None
    login = auth_as(ctx.client, "professional", professional_id=fresh["id"])
    enabled = ctx.client.put("/professionals/me/remote", headers=login, json={"remote_enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["remote_fee_cents"] == FEE
    item = ctx.book(MON_9, professional_id=fresh["id"]).json()
    assert item["remote"] is True and item["professional_price_cents"] == FEE


def test_staff_edits_the_offer_on_the_professional_form(ctx):
    payload = {
        "name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": FEE,
        "on_call_enabled": True, "on_call_price_cents": 9000, "urgent_enabled": True, "urgent_price_cents": URGENT_RATE,
        "remote_enabled": False,
    }
    updated = ctx.client.put(f"/professionals/{ctx.professional['id']}", headers=ctx.headers, json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["remote_enabled"] is False
    assert ctx.book(MON_9).status_code == 400
    assert ctx.client.post("/professionals", headers=ctx.headers, json={**payload, "name": "Neg", "remote_price_cents": -1}).status_code == 422


# --- booking ---------------------------------------------------------------------------


def test_in_person_is_the_default(ctx):
    item = ctx.client.post(
        "/appointments", headers=ctx.headers, json={"customer_id": ctx.holder, "professional_id": ctx.professional["id"], "marked_at": MON_9.isoformat()}
    ).json()
    assert item["remote"] is False and item["meeting_url"] == ""
    assert item["professional_price_cents"] == FEE


def test_remote_appointment_takes_a_regular_slot_and_gets_a_meeting_link(ctx):
    created = ctx.book(MON_9)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["remote"] is True and item["on_call"] is False and item["urgent"] is False
    assert item["meeting_url"].startswith("https://meet.jit.si/HollicHealth-")
    # Every remote appointment has its own room.
    other = ctx.book(MON_10).json()
    assert other["meeting_url"] != item["meeting_url"]
    # The patient and the professional see the link.
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    assert ctx.client.get(f"/appointments/{item['id']}", headers=client_headers).json()["meeting_url"] == item["meeting_url"]
    assert ctx.client.get(f"/appointments/{item['id']}", headers=ctx.login).json()["meeting_url"] == item["meeting_url"]


def test_remote_appointment_follows_the_working_hours(ctx):
    refused = ctx.book(WED_NIGHT)
    assert refused.status_code == 400 and "does not work on" in refused.json()["detail"]
    ctx.client.post(f"/professionals/{ctx.professional['id']}/time-off", headers=ctx.headers, json={"date": "2026-10-27"})
    assert ctx.book(TUE_9).status_code == 400  # time off


def test_remote_appointment_is_refused_during_on_call_shifts(ctx):
    refused = ctx.book(MON_20)
    assert refused.status_code == 409
    assert "remote appointments are not accepted during on-call shifts" in refused.json()["detail"]
    assert ctx.book(MON_20, remote=False).json()["on_call"] is True  # the slot still takes an on-call booking


def test_remote_appointment_cannot_be_urgent(ctx):
    refused = ctx.book(MON_9, urgent=True)
    assert refused.status_code == 400 and "cannot be urgent" in refused.json()["detail"]
    assert ctx.quote(MON_9, urgent=True).status_code == 400


def test_remote_appointment_respects_conflicts_capacity_and_plan(ctx):
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.headers, other, plan=ctx.plan)
    assert ctx.book(MON_9).status_code == 201
    assert ctx.book(MON_9, customer_id=other).status_code == 409  # professional busy
    assert ctx.book(MON_9, remote=False, customer_id=other).status_code == 409  # whatever the kind
    limited = ctx.new_professional("Busy", daily_limit=1).json()
    assert ctx.book(TUE_9, professional_id=limited["id"]).status_code == 201
    refused = ctx.book(TUE_9.replace(hour=10), professional_id=limited["id"], customer_id=other)
    assert refused.status_code == 409 and "fully booked" in refused.json()["detail"]
    uncovered = ctx.client.post("/customers", headers=ctx.headers, json={"name": "No Plan"}).json()["id"]
    assert ctx.book(MON_10, customer_id=uncovered).status_code == 409


def test_client_books_its_own_remote_appointment(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    created = ctx.client.post(
        "/appointments",
        headers=client_headers,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional["id"], "marked_at": MON_9.isoformat(), "remote": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["remote"] is True and created.json()["meeting_url"]
    # The link is the system's (or the staff's), never the patient's.
    refused = ctx.client.post(
        "/appointments",
        headers=client_headers,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional["id"], "marked_at": MON_10.isoformat(), "remote": True, "meeting_url": "https://evil.example/x"},
    )
    assert refused.status_code == 403


# --- pricing -----------------------------------------------------------------------------


def test_remote_price_list_falls_back_to_the_regular_values(ctx):
    item = ctx.book(MON_9).json()
    assert (item["fee_cents"], item["specialty_price_cents"], item["professional_price_cents"]) == (2000, 8000, REMOTE_RATE)
    assert item["gross_cents"] == 14000
    assert item["discount_percent"] == 20  # the plan discount applies as usual
    assert item["amount_cents"] == 11200


def test_clinic_and_specialty_remote_values_apply_when_set(ctx):
    assert ctx.client.put(
        "/clinics/1", headers=ctx.headers, json={"name": "Clinic Foo", "city": "Sample", "fee_cents": 2000, "remote_fee_cents": 0}
    ).status_code == 200
    assert ctx.client.put(
        "/specialties/1", headers=ctx.headers, json={"name": "Clínico geral", "price_cents": 8000, "remote_price_cents": 6000}
    ).status_code == 200
    item = ctx.book(MON_9).json()
    assert (item["fee_cents"], item["specialty_price_cents"], item["professional_price_cents"]) == (0, 6000, REMOTE_RATE)
    assert item["gross_cents"] == 10000 and item["amount_cents"] == 8000
    # In-person appointments keep the regular list.
    person = ctx.book(MON_10, remote=False).json()
    assert (person["fee_cents"], person["specialty_price_cents"], person["professional_price_cents"]) == (2000, 8000, FEE)
    me = ctx.client.get("/professionals/me", headers=ctx.login).json()
    assert (me["clinic_remote_fee_cents"], me["specialty_remote_price_cents"]) == (0, 6000)


def test_quote_previews_the_remote_price_list_and_its_rules(ctx):
    quoted = ctx.quote(MON_9)
    assert quoted.status_code == 200, quoted.text
    assert quoted.json()["remote"] is True
    assert quoted.json()["professional_price_cents"] == REMOTE_RATE
    assert quoted.json()["gross_cents"] == 14000
    assert ctx.quote(MON_9, remote=False).json()["professional_price_cents"] == FEE
    assert ctx.quote(MON_20).status_code == 409  # on-call shift
    ctx.set_remote(remote_enabled=False)
    assert ctx.quote(MON_9).status_code == 400


def test_switching_between_remote_and_in_person_reprices_and_handles_the_link(ctx):
    item = ctx.book(MON_9).json()
    link = item["meeting_url"]
    person = ctx.edit(item, remote=False)
    assert person.status_code == 200, person.text
    assert person.json()["remote"] is False
    assert person.json()["professional_price_cents"] == FEE
    assert person.json()["meeting_url"] == ""
    back = ctx.edit(person.json(), remote=True).json()
    assert back["professional_price_cents"] == REMOTE_RATE
    assert back["meeting_url"].startswith("https://meet.jit.si/") and back["meeting_url"] != link  # a fresh room


def test_moving_a_remote_appointment_keeps_its_link_and_price_snapshot(ctx):
    item = ctx.book(MON_9).json()
    ctx.set_remote(remote_enabled=True, remote_price_cents=3000)
    moved = ctx.edit(item, marked_at=TUE_9)
    assert moved.status_code == 200, moved.text
    assert moved.json()["meeting_url"] == item["meeting_url"]
    assert moved.json()["professional_price_cents"] == REMOTE_RATE  # snapshot kept
    assert ctx.book(MON_10).json()["professional_price_cents"] == 3000  # new bookings use the new rate


def test_staff_may_supply_its_own_meeting_link(ctx):
    created = ctx.book(MON_9, meeting_url="https://meet.google.com/abc-defg-hij")
    assert created.status_code == 201, created.text
    assert created.json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    changed = ctx.edit(created.json(), meeting_url="https://zoom.us/j/123")
    assert changed.json()["meeting_url"] == "https://zoom.us/j/123"
    kept = ctx.edit(changed.json(), notes="x")  # omitted: the link stays
    assert kept.json()["meeting_url"] == "https://zoom.us/j/123"
    assert ctx.book(MON_10, meeting_url="not a link").status_code == 422
    # On an in-person appointment the link is ignored.
    person = ctx.book(MON_10, remote=False, meeting_url="https://zoom.us/j/999").json()
    assert person["meeting_url"] == ""


def test_omitting_the_flag_on_edit_keeps_the_kind_of_appointment(ctx):
    item = ctx.book(MON_9).json()
    payload = {key: item[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    cancelled = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["remote"] is True
    assert cancelled.json()["meeting_url"] == item["meeting_url"]
    back = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**payload, "status": "agendada"})
    assert back.status_code == 200, back.text
    assert back.json()["remote"] is True and back.json()["professional_price_cents"] == REMOTE_RATE


def test_signed_remote_appointment_is_frozen(ctx):
    item = ctx.book(MON_9).json()
    sign(ctx.client, item["id"])
    assert ctx.edit(item, status="realizada", remote=False).status_code == 409
    assert ctx.edit(item, status="realizada", notes="ok").status_code == 200  # notes still editable


# --- agenda, dashboard, payout, reports, notifications -----------------------------------


def test_agenda_flags_remote_bookings(ctx):
    ctx.book(MON_9)
    ctx.book(MON_10, remote=False)
    days = {d["date"]: d for d in ctx.client.get(f"/professionals/{ctx.professional['id']}/slots?start=2026-10-26&end=2026-10-26", headers=ctx.headers).json()}
    monday = {s["time"]: s for s in days["2026-10-26"]["slots"]}
    assert monday["09:00"]["status"] == "booked" and monday["09:00"]["remote"] is True
    assert monday["10:00"]["status"] == "booked" and monday["10:00"]["remote"] is False
    assert monday["11:00"]["status"] == "available" and monday["11:00"]["remote"] is False


def test_dashboard_lists_the_link_of_upcoming_remote_appointments(ctx):
    item = ctx.book(MON_9).json()
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    home = ctx.client.get("/dashboard", headers=client_headers).json()
    (row,) = [r for r in home["next_appointments"] if r["id"] == item["id"]]
    assert row["remote"] is True and row["meeting_url"] == item["meeting_url"]


def test_professional_is_paid_the_remote_rate(ctx):
    remote = ctx.book(MON_9).json()
    regular = ctx.book(MON_10, remote=False).json()
    sign(ctx.client, remote["id"])
    sign(ctx.client, regular["id"])
    statement = ctx.client.get(f"/reports/payouts/{ctx.professional['id']}?start=2026-10-26&end=2026-10-31", headers=ctx.headers).json()
    assert statement["payout_cents"] == REMOTE_RATE + FEE
    assert (statement["remote"], statement["remote_cents"], statement["remote_fee_cents"]) == (1, REMOTE_RATE, REMOTE_RATE)
    lines = {line["appointment_id"]: line for line in statement["lines"]}
    assert lines[remote["id"]]["remote"] is True and lines[remote["id"]]["payout_cents"] == REMOTE_RATE
    assert lines[regular["id"]]["remote"] is False
    report = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.headers).json()
    (row,) = report["professionals"]
    assert (row["remote"], row["remote_cents"], row["remote_fee_cents"]) == (1, REMOTE_RATE, REMOTE_RATE)
    csv = ctx.client.get("/reports/payouts.csv?start=2026-10-26&end=2026-10-31", headers=ctx.headers).text
    header, *rows = csv.strip().splitlines()
    column = header.split(";").index("remote")
    assert sorted(line.split(";")[column] for line in rows) == ["no", "yes"]
    own = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.login).json()
    assert own["professionals"][0]["remote_cents"] == REMOTE_RATE


def test_appointments_report_counts_remote_appointments(ctx):
    ctx.book(MON_9)
    ctx.book(MON_10, remote=False)
    body = ctx.client.get("/reports/appointments?granularity=week&start=2026-10-26&end=2026-11-01", headers=ctx.headers).json()
    assert body["totals"]["remote"] == 1
    assert body["totals"]["remote_cents"] == 11200
    assert body["by_professional"][0]["remote"] == 1
    assert body["periods"][0]["remote"] == 1


def test_notifications_carry_the_link_instead_of_the_address(ctx, outbox):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    outbox.sent.clear()
    item = ctx.book(MON_9).json()
    mine = ctx.client.get("/notifications", headers=client_headers).json()
    assert mine[0]["title"] == "Remote appointment scheduled for 26/10/2026 at 09:00"
    assert "Remote appointment: yes" in mine[0]["body"]
    assert f"Join the call: {item['meeting_url']}" in mine[0]["body"]
    assert "Map:" not in mine[0]["body"] and "How to get there" not in mine[0]["body"]
    theirs = ctx.client.get("/notifications", headers=ctx.login).json()
    assert theirs[0]["title"].startswith("New remote appointment:")
    assert item["meeting_url"] in theirs[0]["body"]
    # The in-person details are unchanged.
    person = ctx.book(MON_10, remote=False).json()
    mine = ctx.client.get("/notifications", headers=client_headers).json()
    assert mine[0]["title"] == "Appointment scheduled for 26/10/2026 at 10:00"
    assert "Where: Clinic Foo" in mine[0]["body"] and "Join the call" not in mine[0]["body"]


def test_switching_the_kind_tells_the_patient(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    item = ctx.book(MON_9, remote=False).json()
    changed = ctx.edit(item, remote=True).json()
    mine = ctx.client.get("/notifications", headers=client_headers).json()
    assert mine[0]["title"] == "Remote appointment on 26/10/2026 at 09:00: now a video call"
    assert "Previously: in person" in mine[0]["body"] and changed["meeting_url"] in mine[0]["body"]
    ctx.edit(changed, remote=False)
    mine = ctx.client.get("/notifications", headers=client_headers).json()
    assert mine[0]["title"] == "Appointment on 26/10/2026 at 09:00: now in person"


def test_reminders_include_the_link(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    item = ctx.book(MON_9).json()
    ctx.client.post("/notifications/read-all", headers=client_headers)
    db = ctx.client.session_factory()
    try:
        assert schedule_reminders(db, MON_9 - timedelta(hours=23)) == {"day": 1, "hour": 0}
    finally:
        db.close()
    mine = ctx.client.get("/notifications?unread=true", headers=client_headers).json()
    reminder = [n for n in mine if n["event"] == "appointment.reminder_day"]
    assert reminder and item["meeting_url"] in reminder[0]["body"]


def test_remote_visit_is_started_with_the_card_and_signed_like_any_other(ctx):
    """The attendance trail is the same: check-in (joining the call), the
    card code read over the call, check-out and the signature."""
    from app import clock

    item = ctx.book(MON_9).json()
    clock.travel_to(MON_9 - timedelta(minutes=10))
    try:
        checked = ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.login)
        assert checked.status_code == 200, checked.text
        from conftest import start

        started = start(ctx.client, item, ctx.login)
        assert started["attendance"] == "in_progress"
        clock.travel_to(MON_9 + timedelta(minutes=25))
        out = ctx.client.post(f"/appointments/{item['id']}/check-out", headers=ctx.login)
        assert out.status_code == 200, out.text
        assert out.json()["awaiting_signature"] is True and out.json()["remote"] is True
    finally:
        pass

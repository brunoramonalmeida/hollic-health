"""Addresses (clinic/company/map links) and the check-in / check-out audit."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.addresses import format_address, maps_url, osm_url
from app.attendance import (
    CHECKIN_CLOSES_AFTER,
    CHECKIN_OPENS_BEFORE,
    AttendanceError,
    assert_can_check_in,
    assert_can_check_out,
    attendance_state,
    can_check_in,
    is_no_show,
)
from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock

NOW = datetime(2026, 10, 26, 8, 0)


# --- addresses (pure) -----------------------------------------------------------------


def test_format_address_full_and_partial():
    full = SimpleNamespace(street="Rua Exemplo", number="100", complement="Sala 2", district="Centro", city="Sample", state="SP", zip_code="00000-000")
    assert format_address(full) == "Rua Exemplo, 100 - Sala 2 - Centro, Sample/SP, 00000-000"
    partial = SimpleNamespace(street="", number="", complement="", district="", city="Sample", state="SP", zip_code="")
    assert format_address(partial) == "Sample/SP"
    no_number = SimpleNamespace(street="Av. Brasil", number="", complement="", district="", city="", state="", zip_code="")
    assert format_address(no_number) == "Av. Brasil"


def test_map_links_prefer_coordinates():
    located = SimpleNamespace(street="Rua X", number="1", complement="", district="", city="Sample", state="SP", zip_code="", latitude=-23.55, longitude=-46.63)
    assert maps_url(located) == "https://www.google.com/maps/search/?api=1&query=-23.55,-46.63"
    assert osm_url(located).startswith("https://www.openstreetmap.org/?mlat=-23.55&mlon=-46.63")
    unlocated = SimpleNamespace(street="Rua X", number="1", complement="", district="", city="Sample", state="SP", zip_code="", latitude=None, longitude=None)
    assert maps_url(unlocated) == "https://www.google.com/maps/search/?api=1&query=Rua+X%2C+1%2C+Sample%2FSP"
    assert osm_url(unlocated) == ""


# --- attendance rules (pure) ----------------------------------------------------------


def appt(marked_at=NOW, status="agendada", checked_in_at=None, checked_out_at=None, started_at=None):
    return SimpleNamespace(marked_at=marked_at, status=status, checked_in_at=checked_in_at, checked_out_at=checked_out_at, started_at=started_at)


def test_check_in_window():
    assert can_check_in(appt(), NOW - CHECKIN_OPENS_BEFORE) is True
    assert can_check_in(appt(), NOW - CHECKIN_OPENS_BEFORE - timedelta(minutes=1)) is False
    assert can_check_in(appt(), NOW + CHECKIN_CLOSES_AFTER) is True
    assert can_check_in(appt(), NOW + CHECKIN_CLOSES_AFTER + timedelta(minutes=1)) is False
    assert can_check_in(appt(status="cancelada"), NOW) is False
    assert can_check_in(appt(checked_in_at=NOW), NOW) is False


def test_check_in_errors_explain_the_window():
    with pytest.raises(AttendanceError, match="opens at 26/10/2026 06:00"):
        assert_can_check_in(appt(), NOW - timedelta(hours=3))
    with pytest.raises(AttendanceError, match="closed at 26/10/2026 10:00"):
        assert_can_check_in(appt(), NOW + timedelta(hours=3))
    with pytest.raises(AttendanceError, match="cancelled"):
        assert_can_check_in(appt(status="cancelada"), NOW)
    with pytest.raises(AttendanceError, match="Already checked in"):
        assert_can_check_in(appt(checked_in_at=NOW - timedelta(minutes=5)), NOW)


def test_check_out_requires_check_in_first():
    with pytest.raises(AttendanceError, match="Check in before"):
        assert_can_check_out(appt(), NOW)
    assert_can_check_out(appt(checked_in_at=NOW - timedelta(minutes=10)), NOW)
    with pytest.raises(AttendanceError, match="Already checked out"):
        assert_can_check_out(appt(checked_in_at=NOW, checked_out_at=NOW), NOW + timedelta(minutes=1))
    with pytest.raises(AttendanceError, match="precede"):
        assert_can_check_out(appt(checked_in_at=NOW), NOW - timedelta(minutes=1))


def test_attendance_states_and_no_show():
    assert attendance_state(appt(), NOW) == "awaiting"
    assert attendance_state(appt(checked_in_at=NOW), NOW) == "checked_in"
    assert attendance_state(appt(status="realizada", checked_in_at=NOW, checked_out_at=NOW), NOW) == "checked_out"
    assert attendance_state(appt(checked_in_at=NOW, started_at=NOW), NOW) == "in_progress"
    assert attendance_state(appt(status="realizada"), NOW) == "completed"
    assert attendance_state(appt(status="cancelada"), NOW) == "cancelled"
    late = NOW + CHECKIN_CLOSES_AFTER + timedelta(minutes=1)
    assert is_no_show(appt(), late) is True
    assert attendance_state(appt(), late) == "no_show"
    assert is_no_show(appt(checked_in_at=NOW), late) is False
    assert is_no_show(appt(), NOW + CHECKIN_CLOSES_AFTER) is False  # still inside the window


# --- API ----------------------------------------------------------------------------------


def next_slot(minutes_ahead=45):
    """Next 30-minute grid time at least `minutes_ahead` from now."""
    target = clock.now() + timedelta(minutes=minutes_ahead)
    target = target.replace(second=0, microsecond=0)
    target += timedelta(minutes=(30 - target.minute % 30) % 30)
    if target.hour == 23 and target.minute == 30:
        # The last slot of the test week is 23:00 (hours end 23:30): skip to the next day.
        target += timedelta(minutes=30)
    return target


@pytest.fixture()
def ctx(client):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.attendant = auth_as(client, "attendant")
    foo = client.get("/clinics/1", headers=admin).json()
    located = client.put(
        "/clinics/1",
        headers=admin,
        json={
            **foo,
            "street": "Rua Exemplo",
            "number": "100",
            "complement": "2º andar",
            "district": "Centro",
            "zip_code": "01000-000",
            "state": "SP",
            "latitude": -23.5505,
            "longitude": -46.6333,
            "phone": "(11) 0000-0000",
            "directions": "Entrance by the side street; parking on site.",
        },
    )
    assert located.status_code == 200, located.text
    c.clinic = located.json()
    c.plan = ensure_plan(client, admin)
    # Works every day, all day, so a slot close to "now" always exists.
    c.professional = client.post(
        "/professionals",
        headers=admin,
        json={
            "name": "Lorem Ipsum",
            "specialty_id": 1,
            "clinic_id": 1,
            "price_cents": 5000,
            "working_hours": [{"weekday": w, "start": "00:00", "end": "23:30"} for w in range(7)],
        },
    ).json()["id"]
    c.holder = client.post("/customers", headers=admin, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, admin, c.holder, plan=c.plan)
    c.client_headers = auth_as(client, "client", customer_id=c.holder)

    def book(marked_at, headers=None, status="agendada"):
        response = client.post(
            "/appointments",
            headers=headers or admin,
            json={"customer_id": c.holder, "professional_id": c.professional, "marked_at": marked_at.isoformat(), "status": status},
        )
        assert response.status_code == 201, response.text
        return response.json()

    c.book = book
    return c


def test_clinic_address_and_map_links(ctx):
    clinic = ctx.clinic
    assert clinic["address"] == "Rua Exemplo, 100 - 2º andar - Centro, Sample/SP, 01000-000"
    assert clinic["maps_url"] == "https://www.google.com/maps/search/?api=1&query=-23.5505,-46.6333"
    assert clinic["osm_url"].startswith("https://www.openstreetmap.org/")
    listed = ctx.client.get("/clinics", headers=ctx.client_headers).json()
    assert listed[0]["directions"] == "Entrance by the side street; parking on site."
    half = ctx.client.put("/clinics/1", headers=ctx.admin, json={**clinic, "latitude": -23.5, "longitude": None})
    assert half.status_code == 422


def test_company_address(ctx):
    saved = ctx.client.put(
        "/company",
        headers=ctx.admin,
        json={"name": "Santé Saúde Ltda", "city": "Sample", "state": "SP", "street": "Av. Central", "number": "10", "zip_code": "01000-000", "latitude": -23.5, "longitude": -46.6},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["address"] == "Av. Central, 10, Sample/SP, 01000-000"
    assert saved.json()["maps_url"].endswith("query=-23.5,-46.6")


def test_appointment_carries_the_place_and_the_email_has_the_address(ctx, outbox):
    item = ctx.book(next_slot())
    assert item["clinic_address"] == "Rua Exemplo, 100 - 2º andar - Centro, Sample/SP, 01000-000"
    assert item["clinic_phone"] == "(11) 0000-0000"
    assert item["clinic_directions"].startswith("Entrance")
    assert (item["clinic_latitude"], item["clinic_longitude"]) == (-23.5505, -46.6333)
    assert item["clinic_maps_url"].endswith("query=-23.5505,-46.6333")
    ctx.client.post("/queue/run", headers=ctx.admin)
    mail = next(m for m in outbox.sent if m["subject"].startswith("Appointment scheduled") and m["to"] == "holder@example.com")
    assert "Address: Rua Exemplo, 100 - 2º andar - Centro, Sample/SP, 01000-000" in mail["body"]
    assert "Map: https://www.google.com/maps/search/?api=1&query=-23.5505,-46.6333" in mail["body"]
    assert "How to get there: Entrance by the side street" in mail["body"]
    assert "check-in" in mail["body"]


def test_check_in_and_check_out_flow_by_the_client(ctx):
    item = ctx.book(next_slot())
    assert item["attendance"] == "awaiting"
    assert item["can_check_in"] is True and item["can_check_out"] is False
    checked = ctx.client.post(
        f"/appointments/{item['id']}/check-in", headers=ctx.client_headers, json={"latitude": -23.55, "longitude": -46.63}
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["attendance"] == "checked_in"
    assert body["checked_in_at"] is not None
    assert body["checked_in_by_name"] == f"Client {ctx.holder}"
    assert body["can_check_in"] is False and body["can_check_out"] is True
    assert body["status"] == "agendada"
    again = ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.client_headers)
    assert again.status_code == 409 and "Already checked in" in again.json()["detail"]
    out = ctx.client.post(f"/appointments/{item['id']}/check-out", headers=ctx.client_headers, json={"notes": "All good"})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["attendance"] == "checked_out"
    # The check-out ends the visit; completion is the professional's signature.
    assert body["status"] == "agendada"
    assert body["signed_at"] is None
    assert body["checked_out_by_name"] == f"Client {ctx.holder}"
    assert body["can_check_out"] is False
    assert "[check-out] All good" in body["notes"]
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.client_headers).json()]
    assert titles[0].startswith("Check-out confirmed at")
    assert any(t.startswith("Check-in confirmed at") for t in titles)
    assert not any(t.endswith("completed") for t in titles)


def test_check_in_notifies_the_staff(ctx):
    item = ctx.book(next_slot())
    ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.client_headers)
    staff = ctx.client.get("/notifications", headers=ctx.attendant).json()
    assert staff[0]["event"] == "appointment.checked_in"
    assert staff[0]["title"].startswith("Foo Holder checked in at")
    assert f"Client {ctx.holder}" in staff[0]["body"]


def test_check_in_outside_the_window_is_refused(ctx):
    far = ctx.book(next_slot(minutes_ahead=300))  # 5 h ahead: window not open
    response = ctx.client.post(f"/appointments/{far['id']}/check-in", headers=ctx.admin)
    assert response.status_code == 409
    assert "opens at" in response.json()["detail"]
    assert far["can_check_in"] is False


def test_check_out_requires_check_in_and_cancelled_cannot_check_in(ctx):
    item = ctx.book(next_slot())
    no = ctx.client.post(f"/appointments/{item['id']}/check-out", headers=ctx.admin)
    assert no.status_code == 409 and "Check in before" in no.json()["detail"]
    cancelled = ctx.book(next_slot(minutes_ahead=75), status="cancelada")
    assert ctx.client.post(f"/appointments/{cancelled['id']}/check-in", headers=ctx.admin).status_code == 409
    assert cancelled["attendance"] == "cancelled"


def test_staff_check_in_for_the_patient_and_undo(ctx):
    item = ctx.book(next_slot())
    checked = ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.attendant).json()
    assert checked["checked_in_by_name"] == "Att Endant"
    # Clients cannot undo; staff can, while not checked out.
    assert ctx.client.delete(f"/appointments/{item['id']}/check-in", headers=ctx.client_headers).status_code == 403
    undone = ctx.client.delete(f"/appointments/{item['id']}/check-in", headers=ctx.attendant)
    assert undone.status_code == 200
    assert undone.json()["attendance"] == "awaiting" and undone.json()["checked_in_at"] is None
    assert ctx.client.delete(f"/appointments/{item['id']}/check-in", headers=ctx.attendant).status_code == 409
    ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.attendant)
    ctx.client.post(f"/appointments/{item['id']}/check-out", headers=ctx.attendant)
    assert ctx.client.delete(f"/appointments/{item['id']}/check-in", headers=ctx.attendant).status_code == 409


def test_client_cannot_touch_another_customers_attendance(ctx):
    other = ctx.client.post("/customers", headers=ctx.admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.admin, other, plan=ctx.plan)
    foreign = ctx.client.post(
        "/appointments", headers=ctx.admin, json={"customer_id": other, "professional_id": ctx.professional, "marked_at": next_slot(75).isoformat()}
    ).json()
    assert ctx.client.post(f"/appointments/{foreign['id']}/check-in", headers=ctx.client_headers).status_code == 403
    assert ctx.client.post(f"/appointments/{foreign['id']}/check-out", headers=ctx.client_headers).status_code == 403


def test_nobody_completes_without_the_professional_signature(ctx):
    item = ctx.book(next_slot())
    payload = {"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": item["marked_at"], "status": "realizada"}
    for headers in (ctx.client_headers, ctx.attendant, ctx.admin):
        response = ctx.client.put(f"/appointments/{item['id']}", headers=headers, json=payload)
        assert response.status_code == 403
        assert "sign" in response.json()["detail"]
    # Signed without the check-in flow: the audit shows it as completed without check-in.
    sign(ctx.client, item["id"])
    assert ctx.client.get(f"/appointments/{item['id']}", headers=ctx.admin).json()["attendance"] == "completed"


def test_no_show_is_derived_for_past_unconfirmed_appointments(ctx):
    # The test clock reads Thursday 2026-10-22 10:00: next Monday is still to come...
    future = ctx.book(datetime(2026, 10, 26, 8, 0))
    assert ctx.client.get(f"/appointments/{future['id']}", headers=ctx.admin).json()["attendance"] == "awaiting"
    # ...and a slot of last Tuesday nobody checked in for is a no-show.
    past = ctx.book(datetime(2026, 10, 20, 8, 0))
    assert ctx.client.get(f"/appointments/{past['id']}", headers=ctx.admin).json()["attendance"] == "no_show"


def test_attendance_report_and_dashboard(ctx):
    slot = next_slot()
    done = ctx.book(slot)
    ctx.client.post(f"/appointments/{done['id']}/check-in", headers=ctx.admin)
    ctx.client.post(f"/appointments/{done['id']}/check-out", headers=ctx.admin)
    waiting = ctx.book(next_slot(75))
    ctx.client.post(f"/appointments/{waiting['id']}/check-in", headers=ctx.client_headers)
    manual = ctx.book(next_slot(105))
    sign(ctx.client, manual["id"])  # signed without the check-in flow
    ctx.book(next_slot(135))  # awaiting
    day = slot.date().isoformat()
    end = (slot + timedelta(hours=3)).date().isoformat()
    report = ctx.client.get(f"/reports/attendance?granularity=week&start={day}&end={end}", headers=ctx.admin).json()
    totals = report["totals"]
    assert totals["scheduled"] == 4
    assert totals["checked_in"] == 2
    assert totals["checked_out"] == 1
    assert totals["completed_without_checkin"] == 1
    assert totals["awaiting"] == 1
    assert totals["no_shows"] == 0
    assert totals["attendance_rate"] == 1.0
    assert totals["avg_arrival_minutes"] is not None
    assert totals["avg_visit_minutes"] is not None and totals["avg_visit_minutes"] >= 0
    assert report["by_clinic"][0]["period"] == "Clinic Foo"
    assert report["by_professional"][0]["scheduled"] == 4
    assert ctx.client.get("/reports/attendance", headers=auth_as(ctx.client, "supervisor")).status_code == 200
    assert ctx.client.get("/reports/attendance", headers=ctx.attendant).status_code == 403
    dashboard = ctx.client.get("/dashboard", headers=ctx.admin).json()
    assert dashboard["checked_in_today"] >= 1  # the slot is later today on the test clock
    assert "no_shows_month" in dashboard

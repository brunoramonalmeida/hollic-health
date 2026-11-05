"""API tests for the weekly/monthly reports."""

from datetime import date, datetime

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock

TODAY = clock.today()
# All appointments are booked "now" (created_at) for dates in October 2026.
WEEK_43 = [datetime(2026, 10, 19, 8, 0), datetime(2026, 10, 20, 8, 0)]  # Mon, Tue of ISO week 43
WEEK_44 = [datetime(2026, 10, 26, 8, 0)]


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    attendant = auth_as(client, "attendant")
    plan = ensure_plan(client, admin, discount_percent=20)  # gross 15000 -> amount 12000
    clinic_b = client.post("/clinics", headers=admin, json={"name": "Clinic Bar", "city": "X", "fee_cents": 2000}).json()
    pro_a = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    pro_b = client.post(
        "/professionals",
        headers=admin,
        json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": clinic_b["id"], "price_cents": 5000},
    ).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)  # sold by admin
    activate_plan(client, attendant, other, plan=plan)  # sold by attendant

    def book(headers, customer, professional, when, status="agendada"):
        response = client.post(
            "/appointments",
            headers=headers,
            json={"customer_id": customer, "professional_id": professional, "marked_at": when.isoformat(), "status": status if status != "realizada" else "agendada"},
        )
        assert response.status_code == 201, response.text
        if status == "realizada":
            sign(client, response.json()["id"])
        return response.json()

    # Week 43: attendant books two for Lorem (one completed), admin books one for Dolor at Clinic Bar.
    book(attendant, holder, pro_a, WEEK_43[0], "realizada")
    book(attendant, holder, pro_a, WEEK_43[1])
    book(admin, other, pro_b, WEEK_43[0])
    # Week 44: one cancelled by the attendant (counted, no money), one booked by admin.
    book(attendant, other, pro_a, WEEK_44[0], "cancelada")
    book(admin, holder, pro_b, WEEK_44[0])

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.attendant = attendant
    c.pro_a, c.pro_b, c.clinic_b = pro_a, pro_b, clinic_b["id"]
    c.plan = plan
    return c


def test_appointments_report_by_week(ctx):
    response = ctx.client.get(
        "/reports/appointments?granularity=week&start=2026-10-19&end=2026-11-01", headers=ctx.admin
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["period"] for p in body["periods"]] == ["2026-W43", "2026-W44"]
    w43, w44 = body["periods"]
    assert (w43["scheduled"], w43["completed"], w43["cancelled"]) == (2, 1, 0)
    assert w43["gross_cents"] == 3 * 15000
    assert w43["discount_cents"] == 3 * 3000
    assert w43["amount_cents"] == 3 * 12000
    assert (w44["scheduled"], w44["completed"], w44["cancelled"]) == (1, 0, 1)
    assert w44["amount_cents"] == 12000  # cancelled adds nothing
    assert body["totals"]["amount_cents"] == 4 * 12000
    assert body["totals"]["cancelled"] == 1


def test_appointments_report_breakdowns(ctx):
    body = ctx.client.get(
        "/reports/appointments?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.admin
    ).json()
    assert [p["period"] for p in body["periods"]] == ["2026-10"]
    by_pro = {row["name"]: row for row in body["by_professional"]}
    assert by_pro["Lorem Ipsum"]["appointments"] == 3
    assert by_pro["Lorem Ipsum"]["cancelled"] == 1
    assert by_pro["Lorem Ipsum"]["amount_cents"] == 2 * 12000
    assert by_pro["Dolor Sit"]["amount_cents"] == 2 * 12000
    by_clinic = {row["name"]: row for row in body["by_clinic"]}
    assert by_clinic["Clinic Bar"]["appointments"] == 2
    assert body["by_specialty"][0]["name"] == "Clínico geral"
    assert body["by_plan"][0]["name"] == "Plan Foo"
    assert body["by_plan"][0]["discount_cents"] == 4 * 3000


def test_appointments_report_filters(ctx):
    by_pro = ctx.client.get(
        f"/reports/appointments?start=2026-10-01&end=2026-10-31&professional_id={ctx.pro_b}", headers=ctx.admin
    ).json()
    assert by_pro["totals"]["scheduled"] == 2
    assert by_pro["totals"]["amount_cents"] == 2 * 12000
    assert [row["name"] for row in by_pro["by_professional"]] == ["Dolor Sit"]
    by_clinic = ctx.client.get(
        f"/reports/appointments?start=2026-10-01&end=2026-10-31&clinic_id={ctx.clinic_b}", headers=ctx.admin
    ).json()
    assert by_clinic["totals"]["scheduled"] == 2
    empty = ctx.client.get("/reports/appointments?start=2026-12-01&end=2026-12-31", headers=ctx.admin).json()
    assert empty["totals"]["amount_cents"] == 0
    assert len(empty["periods"]) >= 4  # empty weeks still listed


def test_report_parameter_validation(ctx):
    assert ctx.client.get("/reports/appointments?granularity=day", headers=ctx.admin).status_code == 400
    assert ctx.client.get("/reports/appointments?start=2026-10-10&end=2026-10-01", headers=ctx.admin).status_code == 400
    assert ctx.client.get("/reports/appointments?start=2020-01-01&end=2026-10-01", headers=ctx.admin).status_code == 400
    defaults = ctx.client.get("/reports/appointments", headers=ctx.admin).json()
    assert defaults["end"] == TODAY.isoformat()
    assert defaults["granularity"] == "week"


def test_attendants_report(ctx):
    # Payments are bucketed by their paid_on date, which activate_plan sets to
    # the due dates (in the past), so the window must reach back to them.
    start = min(TODAY, date(2026, 8, 1)).isoformat()
    body = ctx.client.get(
        f"/reports/attendants?granularity=month&start={start}&end={TODAY.isoformat()}", headers=ctx.admin
    ).json()
    rows = {row["name"]: row for row in body["totals"]}
    attendant = rows["Att Endant"]
    assert attendant["role"] == "attendant"
    assert attendant["appointments_booked"] == 3
    assert attendant["appointments_completed"] == 1
    assert attendant["appointments_cancelled"] == 1
    assert attendant["completion_rate"] == 0.5
    assert attendant["plans_sold"] == 1
    assert attendant["payments_registered"] >= 1
    assert attendant["payments_cents"] >= 9900
    admin = rows["Foo Bar"]
    assert admin["appointments_booked"] == 2
    assert admin["plans_sold"] == 1
    assert body["periods"][-1]["period"] == f"{TODAY.year}-{TODAY.month:02d}"
    assert body["periods"][-1]["rows"]


def test_attendants_report_visible_to_supervisor(ctx):
    supervisor = auth_as(ctx.client, "supervisor")
    response = ctx.client.get("/reports/attendants", headers=supervisor)
    assert response.status_code == 200
    assert response.json()["totals"]


def test_plans_report(ctx):
    body = ctx.client.get(
        f"/reports/plans?granularity=month&start={TODAY.replace(day=1).isoformat()}&end={TODAY.isoformat()}", headers=ctx.admin
    ).json()
    assert body["active_subscriptions"] == 2
    assert body["pending_subscriptions"] == 0
    assert body["totals"]["sold"] == 2
    assert body["totals"]["cancelled"] == 0
    assert body["totals"]["payments"] >= 2
    assert body["totals"]["revenue_cents"] >= 2 * 9900
    assert body["by_plan"][0]["name"] == "Plan Foo"
    assert body["by_plan"][0]["sold"] == 2
    # Cancel one: shows up as cancelled and leaves the active count.
    sub = ctx.client.get("/subscriptions", headers=ctx.admin).json()[0]
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.admin)
    after = ctx.client.get(
        f"/reports/plans?granularity=month&start={TODAY.replace(day=1).isoformat()}&end={TODAY.isoformat()}", headers=ctx.admin
    ).json()
    assert after["active_subscriptions"] == 1
    assert after["totals"]["cancelled"] == 1

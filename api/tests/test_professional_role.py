"""The ``professional`` role: a login tied to a professional record.

It sees its own appointments (and only those), signs them, follows its own
payout and gets its agenda notifications. It does not book appointments nor
browse customers, plans or the management reports.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import DUMMY_SIGN_BODY, activate_plan, auth, auth_as, ensure_plan, sign

MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)
TUESDAY_8 = datetime(2026, 10, 27, 8, 0)


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin)
    lorem = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    dolor = client.post(
        "/professionals", headers=admin, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 7000}
    ).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)
    activate_plan(client, admin, other, plan=plan)

    def book(customer, professional, when):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": customer, "professional_id": professional, "marked_at": when.isoformat()},
        )
        assert response.status_code == 201, response.text
        return response.json()

    c = SimpleNamespace(client=client, admin=admin, lorem=lorem, dolor=dolor, holder=holder, other=other, book=book)
    c.lorem_a = book(holder, lorem, MONDAY_8)
    c.lorem_b = book(other, lorem, MONDAY_9)
    c.dolor_a = book(holder, dolor, TUESDAY_8)
    sign(client, c.lorem_a["id"])  # creates the professional login of Lorem as a side effect
    c.lorem_login = auth_as(client, "professional", professional_id=lorem)
    return c


# --- users -----------------------------------------------------------------------------


def test_admin_creates_a_professional_user_linked_to_a_professional(ctx):
    created = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "Dr. Dolor", "email": "dolor@example.com", "role": "professional", "password": "saude123", "professional_id": ctx.dolor},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "professional"
    assert body["professional_id"] == ctx.dolor and body["professional_name"] == "Dolor Sit"
    assert body["customer_id"] is None
    me = ctx.client.get("/auth/me", headers=auth(ctx.client, "dolor@example.com")).json()
    assert me["role"] == "professional" and me["professional_id"] == ctx.dolor and me["professional_name"] == "Dolor Sit"
    listed = {row["email"]: row for row in ctx.client.get("/users", headers=ctx.admin).json()}
    assert listed["dolor@example.com"]["professional_name"] == "Dolor Sit"


def test_professional_user_validation(ctx):
    base = {"name": "X", "email": "x@example.com", "password": "saude123"}
    missing = ctx.client.post("/users", headers=ctx.admin, json={**base, "role": "professional"})
    assert missing.status_code == 422 and "linked to a professional" in missing.text
    wrong_role = ctx.client.post("/users", headers=ctx.admin, json={**base, "role": "attendant", "professional_id": ctx.lorem})
    assert wrong_role.status_code == 422 and "only professional users" in wrong_role.text
    both = ctx.client.post(
        "/users", headers=ctx.admin, json={**base, "role": "professional", "professional_id": ctx.lorem, "customer_id": ctx.holder}
    )
    assert both.status_code == 422
    unknown = ctx.client.post("/users", headers=ctx.admin, json={**base, "role": "professional", "professional_id": 9999})
    assert unknown.status_code == 404


def test_supervisor_cannot_manage_professional_users(ctx):
    supervisor = auth_as(ctx.client, "supervisor")
    refused = ctx.client.post(
        "/users",
        headers=supervisor,
        json={"name": "X", "email": "x@example.com", "role": "professional", "password": "saude123", "professional_id": ctx.dolor},
    )
    assert refused.status_code == 403
    emails = [row["email"] for row in ctx.client.get("/users", headers=supervisor).json()]
    assert f"professional{ctx.lorem}@example.com" not in emails


# --- own appointments ------------------------------------------------------------------


def test_professional_lists_and_opens_only_own_appointments(ctx):
    rows = ctx.client.get("/appointments", headers=ctx.lorem_login).json()
    assert {row["id"] for row in rows} == {ctx.lorem_a["id"], ctx.lorem_b["id"]}
    assert all(row["professional_id"] == ctx.lorem for row in rows)
    # A customer filter cannot widen it.
    rows = ctx.client.get(f"/appointments?customer_id={ctx.holder}", headers=ctx.lorem_login).json()
    assert {row["id"] for row in rows} == {ctx.lorem_a["id"], ctx.lorem_b["id"]}
    assert ctx.client.get(f"/appointments/{ctx.lorem_a['id']}", headers=ctx.lorem_login).status_code == 200
    assert ctx.client.get(f"/appointments/{ctx.dolor_a['id']}", headers=ctx.lorem_login).status_code == 403
    # Status filter from the UI: completed ones are the signed ones.
    done = [row for row in rows if row["status"] == "realizada"]
    assert [row["id"] for row in done] == [ctx.lorem_a["id"]]
    assert done[0]["signed_by_name"] == f"Professional {ctx.lorem}"


def test_appointments_can_be_listed_by_range_for_the_agenda(ctx):
    """My agenda asks for a week or a month at a time (``start``/``end``, inclusive days)."""
    later = ctx.book(ctx.holder, ctx.lorem, TUESDAY_8 + timedelta(days=7))
    week = ctx.client.get("/appointments?start=2026-10-26&end=2026-11-01", headers=ctx.lorem_login).json()
    assert {row["id"] for row in week} == {ctx.lorem_a["id"], ctx.lorem_b["id"]}
    next_week = ctx.client.get("/appointments?start=2026-11-02&end=2026-11-08", headers=ctx.lorem_login).json()
    assert [row["id"] for row in next_week] == [later["id"]]
    single_day = ctx.client.get("/appointments?start=2026-10-26&end=2026-10-26", headers=ctx.lorem_login).json()
    assert len(single_day) == 2  # the whole day, inclusive
    assert ctx.client.get("/appointments?start=2026-10-27", headers=ctx.lorem_login).json()[0]["id"] == later["id"]
    assert ctx.client.get("/appointments?start=2026-11-02&end=2026-11-01", headers=ctx.lorem_login).status_code == 400
    # The staff ranges too, across professionals.
    assert len(ctx.client.get("/appointments?start=2026-10-27&end=2026-10-27", headers=ctx.admin).json()) == 1


def test_professional_has_no_access_to_customers_plans_or_management(ctx):
    headers = ctx.lorem_login
    assert ctx.client.get("/customers", headers=headers).status_code == 403
    assert ctx.client.get(f"/customers/{ctx.holder}", headers=headers).status_code == 403
    assert ctx.client.get(f"/customers/{ctx.holder}/dependents", headers=headers).status_code == 403
    assert ctx.client.get("/subscriptions", headers=headers).status_code == 403
    assert ctx.client.get(f"/subscriptions/customer/{ctx.holder}", headers=headers).status_code == 403
    assert ctx.client.get(f"/appointments/quote?customer_id={ctx.holder}&professional_id={ctx.lorem}", headers=headers).status_code == 403
    assert ctx.client.get("/users", headers=headers).status_code == 403
    assert ctx.client.get("/tax-rules", headers=headers).status_code == 403
    assert ctx.client.get("/company", headers=headers).status_code == 403
    assert ctx.client.get("/queue", headers=headers).status_code == 403
    for report in ("appointments", "plans", "finance", "taxes", "attendants", "attendance"):
        assert ctx.client.get(f"/reports/{report}", headers=headers).status_code == 403, report
    # Time off and fees stay with the staff.
    assert ctx.client.post(f"/professionals/{ctx.lorem}/time-off", headers=headers, json={"date": "2026-11-02"}).status_code == 403
    current = ctx.client.get(f"/professionals/{ctx.lorem}", headers=headers).json()
    assert ctx.client.put(f"/professionals/{ctx.lorem}", headers=headers, json={**current, "price_cents": 99999}).status_code == 403


def test_professional_can_read_its_agenda_and_catalog(ctx):
    headers = ctx.lorem_login
    assert ctx.client.get(f"/professionals/{ctx.lorem}", headers=headers).status_code == 200
    slots = ctx.client.get(f"/professionals/{ctx.lorem}/slots?start=2026-10-26&end=2026-10-26", headers=headers).json()
    booked = [slot for slot in slots[0]["slots"] if slot["status"] == "booked"]
    assert len(booked) == 2
    assert ctx.client.get("/clinics", headers=headers).status_code == 200
    assert ctx.client.get("/specialties", headers=headers).status_code == 200


# --- own payout ------------------------------------------------------------------------


def test_professional_sees_own_payout_report_and_statement(ctx):
    headers = ctx.lorem_login
    sign(ctx.client, ctx.dolor_a["id"])  # Dolor's money must not show up
    body = ctx.client.get("/reports/payouts?granularity=week&start=2026-10-19&end=2026-11-01", headers=headers).json()
    assert body["professionals_count"] == 1
    row = body["professionals"][0]
    assert row["professional_id"] == ctx.lorem and row["name"] == "Lorem Ipsum"
    assert (row["completed"], row["scheduled"], row["payout_cents"]) == (1, 1, 5000)
    assert body["totals"]["payout_cents"] == 5000
    statement = ctx.client.get(f"/reports/payouts/{ctx.lorem}?start=2026-10-19&end=2026-11-01", headers=headers).json()
    assert [line["payout_cents"] for line in statement["lines"]] == [5000, 0]
    assert statement["lines"][0]["signed_at"] is not None
    assert ctx.client.get(f"/reports/payouts/{ctx.dolor}?start=2026-10-19&end=2026-11-01", headers=headers).status_code == 403
    csv_text = ctx.client.get("/reports/payouts.csv?start=2026-10-19&end=2026-11-01", headers=headers).text
    assert "Lorem Ipsum" in csv_text and "Dolor Sit" not in csv_text


def test_professional_dashboard(ctx):
    body = ctx.client.get("/dashboard", headers=ctx.lorem_login).json()
    assert body["role"] == "professional"
    assert "payout_month_cents" in body and "signed_month" in body and "to_sign" in body
    assert body["subscriptions_active"] == 0 and body["installments_due_week"] == 0
    assert body["revenue_month_cents"] is None
    # Every appointment it counts is its own.
    assert all(row["professional"] == "Lorem Ipsum" for row in body["next_appointments"] + body["to_sign"])


# --- notifications --------------------------------------------------------------------


def test_professional_is_notified_about_its_agenda(ctx):
    headers = ctx.lorem_login
    before = [n["title"] for n in ctx.client.get("/notifications", headers=headers).json()]
    assert not any("Dolor" in title for title in before)
    fresh = ctx.book(ctx.holder, ctx.lorem, MONDAY_9 + timedelta(hours=1))
    base = {"customer_id": ctx.holder, "professional_id": ctx.lorem}
    ctx.client.put(f"/appointments/{fresh['id']}", headers=ctx.admin, json={**base, "marked_at": (MONDAY_9 + timedelta(hours=2)).isoformat()})
    ctx.client.put(f"/appointments/{fresh['id']}", headers=ctx.admin, json={**base, "marked_at": (MONDAY_9 + timedelta(hours=2)).isoformat(), "status": "cancelada"})
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=headers).json()]
    assert titles[:3] == [
        "Cancelled: Foo Holder on 26/10/2026 at 11:00",
        "Moved: Foo Holder to 26/10/2026 at 11:00",
        "New appointment: Foo Holder on 26/10/2026 at 10:00",
    ]
    # Nothing about another professional's agenda.
    ctx.book(ctx.other, ctx.dolor, TUESDAY_8 + timedelta(hours=1))
    assert len(ctx.client.get("/notifications", headers=headers).json()) == len(titles)


# --- RBAC matrix for the signature endpoints --------------------------------------------


@pytest.mark.parametrize("role", ["admin", "supervisor", "attendant", "client"])
def test_signature_endpoints_refuse_other_roles(ctx, role):
    headers = auth_as(ctx.client, role, customer_id=ctx.other)
    assert ctx.client.post(f"/appointments/{ctx.lorem_b['id']}/sign", headers=headers, json=DUMMY_SIGN_BODY).status_code == 403
    assert ctx.client.request("DELETE", f"/appointments/{ctx.lorem_a['id']}/sign", headers=headers, json=DUMMY_SIGN_BODY).status_code == 403

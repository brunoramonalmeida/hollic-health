"""Role-based access control.

admin       everything
supervisor  everything except staff user management and balance reports
attendant   books appointments, manages customers and subscriptions; cannot
            change anything that moves prices (plans, discounts, specialties,
            clinics, professionals' fees/hours)
client      only its own appointments and subscription (read-only elsewhere)
"""

from datetime import datetime

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan

MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)
TUESDAY_8 = datetime(2026, 10, 27, 8, 0)


@pytest.fixture()
def ctx(client):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.plan = ensure_plan(client, admin, discount_percent=10)
    c.professional = client.post(
        "/professionals",
        headers=admin,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000},
    ).json()["id"]
    c.holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    c.other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    c.subscription = activate_plan(client, admin, c.holder, plan=c.plan)
    c.other_subscription = activate_plan(client, admin, c.other, plan=c.plan)
    c.headers = {
        "admin": admin,
        "supervisor": auth_as(client, "supervisor"),
        "attendant": auth_as(client, "attendant"),
        "client": auth_as(client, "client", customer_id=c.holder),
    }

    def appointment(customer_id, marked_at=MONDAY_8):
        return {"customer_id": customer_id, "professional_id": c.professional, "marked_at": marked_at.isoformat()}

    c.appointment = appointment
    return c


ROLES = ["admin", "supervisor", "attendant", "client"]


def test_me_reports_role_and_customer(ctx):
    me = ctx.client.get("/auth/me", headers=ctx.headers["client"]).json()
    assert me["role"] == "client"
    assert me["customer_id"] == ctx.holder
    assert me["customer_name"] == "Foo Holder"
    assert ctx.client.get("/auth/me", headers=ctx.headers["attendant"]).json()["role"] == "attendant"


# --- price-moving catalog: admin & supervisor only ------------------------------------


@pytest.mark.parametrize("role, expected", [("admin", 201), ("supervisor", 201), ("attendant", 403), ("client", 403)])
def test_create_clinic(ctx, role, expected):
    response = ctx.client.post("/clinics", headers=ctx.headers[role], json={"name": f"C {role}", "city": "X", "fee_cents": 1})
    assert response.status_code == expected


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 200), ("attendant", 403), ("client", 403)])
def test_edit_specialty(ctx, role, expected):
    response = ctx.client.put(
        "/specialties/1", headers=ctx.headers[role], json={"name": "Clínico geral", "price_cents": 9000}
    )
    assert response.status_code == expected


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 200), ("attendant", 403), ("client", 403)])
def test_edit_plan_and_discounts(ctx, role, expected):
    plan = ctx.client.put("/modalities/%d" % ctx.plan["id"], headers=ctx.headers[role], json={**ctx.plan, "discount_percent": 30})
    assert plan.status_code == expected
    discounts = ctx.client.put(
        "/modalities/%d/discounts" % ctx.plan["id"], headers=ctx.headers[role], json={"discounts": [{"specialty_id": 1, "percent": 5}]}
    )
    assert discounts.status_code == expected


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 200), ("attendant", 403), ("client", 403)])
def test_edit_professional_fee_and_hours(ctx, role, expected):
    fee = ctx.client.put(
        f"/professionals/{ctx.professional}",
        headers=ctx.headers[role],
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 9999},
    )
    assert fee.status_code == expected
    hours = ctx.client.put(
        f"/professionals/{ctx.professional}/working-hours",
        headers=ctx.headers[role],
        json={"ranges": [{"weekday": 0, "start": "08:00", "end": "12:00"}]},
    )
    assert hours.status_code == expected


@pytest.mark.parametrize("role, expected", [("admin", 201), ("supervisor", 201), ("attendant", 201), ("client", 403)])
def test_time_off_is_staff_work(ctx, role, expected):
    response = ctx.client.post(
        f"/professionals/{ctx.professional}/time-off", headers=ctx.headers[role], json={"date": "2026-11-02"}
    )
    assert response.status_code == expected


@pytest.mark.parametrize("role", ROLES)
def test_everyone_reads_the_catalog_and_agenda(ctx, role):
    headers = ctx.headers[role]
    for path in ("/clinics", "/specialties", "/modalities", "/professionals", f"/professionals/{ctx.professional}/slots?start=2026-10-26"):
        assert ctx.client.get(path, headers=headers).status_code == 200, path


# --- customers ------------------------------------------------------------------------


@pytest.mark.parametrize("role, expected", [("admin", 201), ("supervisor", 201), ("attendant", 201), ("client", 403)])
def test_create_customer(ctx, role, expected):
    assert ctx.client.post("/customers", headers=ctx.headers[role], json={"name": f"N {role}"}).status_code == expected


def test_client_sees_only_its_own_customer_record(ctx):
    headers = ctx.headers["client"]
    listed = ctx.client.get("/customers", headers=headers).json()
    assert [row["id"] for row in listed] == [ctx.holder]
    assert ctx.client.get(f"/customers/{ctx.holder}", headers=headers).status_code == 200
    assert ctx.client.get(f"/customers/{ctx.other}", headers=headers).status_code == 403
    assert ctx.client.get(f"/customers/{ctx.holder}/dependents", headers=headers).status_code == 200
    assert ctx.client.get(f"/customers/{ctx.other}/dependents", headers=headers).status_code == 403
    assert ctx.client.put(f"/customers/{ctx.holder}", headers=headers, json={"name": "Renamed"}).status_code == 403
    assert ctx.client.post(
        f"/customers/{ctx.holder}/dependents", headers=headers, json={"name": "Kid", "birth_date": "2018-01-01"}
    ).status_code == 403


# --- appointments ---------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "supervisor", "attendant"])
def test_staff_book_for_any_customer(ctx, role):
    response = ctx.client.post("/appointments", headers=ctx.headers[role], json=ctx.appointment(ctx.other))
    assert response.status_code == 201
    assert response.json()["created_by_name"] == ctx.client.get("/auth/me", headers=ctx.headers[role]).json()["name"]


def test_client_books_only_for_itself(ctx):
    headers = ctx.headers["client"]
    own = ctx.client.post("/appointments", headers=headers, json=ctx.appointment(ctx.holder))
    assert own.status_code == 201
    assert own.json()["created_by_name"] == f"Client {ctx.holder}"
    foreign = ctx.client.post("/appointments", headers=headers, json=ctx.appointment(ctx.other, MONDAY_9))
    assert foreign.status_code == 403


def test_client_lists_and_edits_only_its_own_appointments(ctx):
    own = ctx.client.post("/appointments", headers=ctx.admin, json=ctx.appointment(ctx.holder)).json()
    foreign = ctx.client.post("/appointments", headers=ctx.admin, json=ctx.appointment(ctx.other, MONDAY_9)).json()
    headers = ctx.headers["client"]
    listed = ctx.client.get("/appointments", headers=headers).json()
    assert [row["id"] for row in listed] == [own["id"]]
    assert ctx.client.get(f"/appointments/{own['id']}", headers=headers).status_code == 200
    assert ctx.client.get(f"/appointments/{foreign['id']}", headers=headers).status_code == 403
    moved = ctx.client.put(f"/appointments/{own['id']}", headers=headers, json=ctx.appointment(ctx.holder, TUESDAY_8))
    assert moved.status_code == 200
    assert ctx.client.put(f"/appointments/{foreign['id']}", headers=headers, json=ctx.appointment(ctx.other, TUESDAY_8)).status_code == 403
    # Handing the appointment over to another customer is not allowed either.
    assert ctx.client.put(f"/appointments/{own['id']}", headers=headers, json=ctx.appointment(ctx.other, TUESDAY_8)).status_code == 403
    # Clients cancel, they do not delete.
    assert ctx.client.delete(f"/appointments/{own['id']}", headers=headers).status_code == 403
    cancelled = ctx.client.put(
        f"/appointments/{own['id']}", headers=headers, json={**ctx.appointment(ctx.holder, TUESDAY_8), "status": "cancelada"}
    )
    assert cancelled.status_code == 200


def test_client_cannot_quote_for_another_customer(ctx):
    headers = ctx.headers["client"]
    assert ctx.client.get(f"/appointments/quote?customer_id={ctx.holder}&professional_id={ctx.professional}", headers=headers).status_code == 200
    assert ctx.client.get(f"/appointments/quote?customer_id={ctx.other}&professional_id={ctx.professional}", headers=headers).status_code == 403


def test_staff_filter_appointments_by_customer(ctx):
    ctx.client.post("/appointments", headers=ctx.admin, json=ctx.appointment(ctx.holder))
    ctx.client.post("/appointments", headers=ctx.admin, json=ctx.appointment(ctx.other, MONDAY_9))
    rows = ctx.client.get(f"/appointments?customer_id={ctx.other}", headers=ctx.headers["attendant"]).json()
    assert [row["customer_id"] for row in rows] == [ctx.other]


# --- subscriptions ----------------------------------------------------------------------


def test_client_manages_only_its_own_subscription(ctx):
    headers = ctx.headers["client"]
    listed = ctx.client.get("/subscriptions", headers=headers).json()
    assert [row["id"] for row in listed] == [ctx.subscription["id"]]
    assert ctx.client.get(f"/subscriptions/{ctx.other_subscription['id']}", headers=headers).status_code == 403
    assert ctx.client.get(f"/subscriptions/customer/{ctx.other}", headers=headers).status_code == 403
    # Registering money is staff only.
    installment = ctx.subscription["installments"][-1]
    pay = ctx.client.put(
        f"/subscriptions/{ctx.subscription['id']}/installments/{installment['id']}", headers=headers, json={"paid_on": "2026-01-01"}
    )
    assert pay.status_code == 403
    assert ctx.client.delete(f"/subscriptions/{ctx.subscription['id']}", headers=headers).status_code == 403
    # Cancelling its own plan is allowed; someone else's is not.
    assert ctx.client.post(f"/subscriptions/{ctx.other_subscription['id']}/cancel", headers=headers).status_code == 403
    assert ctx.client.post(f"/subscriptions/{ctx.subscription['id']}/cancel", headers=headers).status_code == 200
    assert ctx.client.post(f"/subscriptions/{ctx.subscription['id']}/reactivate", headers=headers).status_code == 403
    # And subscribing again for itself works, but not for another customer.
    own = ctx.client.post(
        "/subscriptions",
        headers=headers,
        json={"customer_id": ctx.holder, "modality_id": ctx.plan["id"], "joined_on": "2026-11-01", "expires_on": "2027-10-31"},
    )
    assert own.status_code == 201
    foreign = ctx.client.post(
        "/subscriptions",
        headers=headers,
        json={"customer_id": ctx.other, "modality_id": ctx.plan["id"], "joined_on": "2026-11-01", "expires_on": "2027-10-31"},
    )
    assert foreign.status_code == 403


@pytest.mark.parametrize("role", ["admin", "supervisor", "attendant"])
def test_staff_sell_plans_and_register_payments(ctx, role):
    headers = ctx.headers[role]
    customer = ctx.client.post("/customers", headers=ctx.admin, json={"name": f"Buyer {role}"}).json()["id"]
    sold = ctx.client.post(
        "/subscriptions",
        headers=headers,
        json={"customer_id": customer, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    )
    assert sold.status_code == 201
    installment = sold.json()["installments"][0]
    paid = ctx.client.put(
        f"/subscriptions/{sold.json()['id']}/installments/{installment['id']}", headers=headers, json={"paid_on": "2026-10-01"}
    )
    assert paid.status_code == 200
    assert paid.json()["status"] == "ativa"


# --- users --------------------------------------------------------------------------


def test_admin_manages_every_user(ctx):
    created = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "New Sup", "email": "newsup@example.com", "role": "supervisor", "password": "secret1"},
    )
    assert created.status_code == 201
    listed = ctx.client.get("/users", headers=ctx.admin).json()
    assert {row["role"] for row in listed} >= {"admin", "supervisor", "attendant", "client"}
    promoted = ctx.client.put(
        f"/users/{created.json()['id']}",
        headers=ctx.admin,
        json={"name": "New Sup", "email": "newsup@example.com", "role": "admin"},
    )
    assert promoted.status_code == 200
    assert auth(ctx.client, "newsup@example.com", "secret1")  # password kept when omitted
    assert ctx.client.delete(f"/users/{created.json()['id']}", headers=ctx.admin).status_code == 204


def test_supervisor_manages_only_client_users(ctx):
    headers = ctx.headers["supervisor"]
    listed = ctx.client.get("/users", headers=headers).json()
    assert listed and all(row["role"] == "client" for row in listed)
    staff = ctx.client.post(
        "/users", headers=headers, json={"name": "X", "email": "x@example.com", "role": "attendant", "password": "secret1"}
    )
    assert staff.status_code == 403
    client_user = ctx.client.post(
        "/users",
        headers=headers,
        json={"name": "Other Client", "email": "other@example.com", "role": "client", "password": "secret1", "customer_id": ctx.other},
    )
    assert client_user.status_code == 201
    # Cannot turn a client into staff, cannot touch staff accounts.
    promote = ctx.client.put(
        f"/users/{client_user.json()['id']}",
        headers=headers,
        json={"name": "Other Client", "email": "other@example.com", "role": "attendant"},
    )
    assert promote.status_code == 403
    admin_id = ctx.client.get("/auth/me", headers=ctx.admin).json()["id"]
    assert ctx.client.get(f"/users/{admin_id}", headers=headers).status_code == 403
    assert ctx.client.delete(f"/users/{admin_id}", headers=headers).status_code == 403
    assert ctx.client.delete(f"/users/{client_user.json()['id']}", headers=headers).status_code == 204


@pytest.mark.parametrize("role", ["attendant", "client"])
def test_attendants_and_clients_cannot_manage_users(ctx, role):
    assert ctx.client.get("/users", headers=ctx.headers[role]).status_code == 403


def test_user_validation(ctx):
    bad_role = ctx.client.post("/users", headers=ctx.admin, json={"name": "X", "email": "x@example.com", "role": "boss", "password": "secret1"})
    assert bad_role.status_code == 422
    client_without_customer = ctx.client.post(
        "/users", headers=ctx.admin, json={"name": "X", "email": "x@example.com", "role": "client", "password": "secret1"}
    )
    assert client_without_customer.status_code == 422
    staff_with_customer = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "X", "email": "x@example.com", "role": "attendant", "password": "secret1", "customer_id": ctx.holder},
    )
    assert staff_with_customer.status_code == 422
    no_password = ctx.client.post("/users", headers=ctx.admin, json={"name": "X", "email": "x@example.com", "role": "attendant"})
    assert no_password.status_code == 400
    duplicate = ctx.client.post(
        "/users", headers=ctx.admin, json={"name": "X", "email": "attendant@example.com", "role": "attendant", "password": "secret1"}
    )
    assert duplicate.status_code == 409
    me = ctx.client.get("/auth/me", headers=ctx.admin).json()
    assert ctx.client.delete(f"/users/{me['id']}", headers=ctx.admin).status_code == 400


# --- reports --------------------------------------------------------------------------


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 403), ("attendant", 403), ("client", 403)])
def test_balance_reports_are_admin_only(ctx, role, expected):
    assert ctx.client.get("/reports/appointments", headers=ctx.headers[role]).status_code == expected
    assert ctx.client.get("/reports/plans", headers=ctx.headers[role]).status_code == expected
    assert ctx.client.get("/reports/payouts", headers=ctx.headers[role]).status_code == expected


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 200), ("attendant", 403), ("client", 403)])
def test_attendant_report_for_managers(ctx, role, expected):
    assert ctx.client.get("/reports/attendants", headers=ctx.headers[role]).status_code == expected

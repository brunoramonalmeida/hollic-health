"""API tests for the partner companies' self-service portal (role
``partner``): the company's logins, its home, its agreements and invoices,
enrolling and removing its employees, privacy, and everything else of the
network kept out of reach."""

from datetime import date, datetime

import pytest

from app import clock
from conftest import auth, auth_as, ensure_plan

STARTS = date(2026, 10, 1)
ENDS = date(2027, 3, 31)
TODAY = date(2026, 10, 22)  # the anchored clock


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.outbox = outbox
    c.plan = ensure_plan(client, admin)  # 99.00/month
    c.other_plan = ensure_plan(client, admin, name="Plan Bar", plan_value_cents=19900)

    def partner(name="Acme Indústria", **fields):
        response = client.post("/partners", headers=admin, json={"name": name, "email": f"rh@{name.split()[0].lower()}.example", **fields})
        assert response.status_code == 201, response.text
        return response.json()

    def agreement(partner_id, name="Acme 2026/2", **fields):
        body = {
            "partner_id": partner_id,
            "name": name,
            "starts_on": STARTS.isoformat(),
            "min_employees": 2,
            "payer": "company",
            "plans": [{"modality_id": c.plan["id"], "discount_percent": 20, "grace_days": 0}],
            **fields,
        }
        response = client.post("/agreements", headers=admin, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def login(partner_id, email=None, headers=None, expect=201):
        email = email or f"hr{partner_id}@company.example"
        response = client.post(
            "/users",
            headers=headers or admin,
            json={"name": "HR Manager", "email": email, "role": "partner", "password": "saude123", "partner_id": partner_id},
        )
        assert response.status_code == expect, response.text
        if expect != 201:
            return None
        return auth(client, email)

    def enroll(headers, agreement_id, expect=201, **fields):
        body = {"agreement_id": agreement_id, "modality_id": c.plan["id"], "name": "Ana Employee", "document": "123.456.789-01", **fields}
        response = client.post("/portal/employees", headers=headers, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    c.acme = partner()
    c.terms = agreement(c.acme["id"])
    c.hr = login(c.acme["id"])
    c.globex = partner("Globex Serviços")
    c.globex_terms = agreement(c.globex["id"], name="Globex 2026/2")
    c.partner = partner
    c.agreement = agreement
    c.login = login
    c.enroll = enroll
    return c


# --- logins --------------------------------------------------------------------------


def test_company_logins_are_created_by_admins_and_supervisors(ctx):
    me = ctx.client.get("/auth/me", headers=ctx.hr).json()
    assert (me["role"], me["partner_id"], me["partner_name"]) == ("partner", ctx.acme["id"], "Acme Indústria")
    ctx.login(ctx.acme["id"], "second@acme.example", headers=auth_as(ctx.client, "supervisor"))
    ctx.login(ctx.acme["id"], "third@acme.example", headers=auth_as(ctx.client, "attendant"), expect=403)
    # A company login is tied to a company, and only a company login is.
    bad = {"name": "X", "email": "x@acme.example", "role": "partner", "password": "saude123"}
    assert ctx.client.post("/users", headers=ctx.admin, json=bad).status_code == 422
    bad = {"name": "X", "email": "x@acme.example", "role": "attendant", "password": "saude123", "partner_id": ctx.acme["id"]}
    assert ctx.client.post("/users", headers=ctx.admin, json=bad).status_code == 422
    bad = {"name": "X", "email": "x@acme.example", "role": "partner", "password": "saude123", "partner_id": 999}
    assert ctx.client.post("/users", headers=ctx.admin, json=bad).status_code == 404
    listed = ctx.client.get("/users", headers=auth_as(ctx.client, "supervisor")).json()
    assert {row["role"] for row in listed} <= {"client", "partner"} and "partner" in {row["role"] for row in listed}


def test_a_deactivated_company_loses_its_access_at_once(ctx):
    assert ctx.client.get("/portal", headers=ctx.hr).status_code == 200
    ctx.client.put(f"/partners/{ctx.acme['id']}", headers=ctx.admin, json={"name": ctx.acme["name"], "active": False})
    response = ctx.client.get("/portal", headers=ctx.hr)
    assert response.status_code == 401
    assert response.json()["detail"] == "Your company's access was deactivated; contact Hollic Health"
    refused = ctx.client.post("/auth/token", json={"email": f"hr{ctx.acme['id']}@company.example", "password": "saude123"})
    assert refused.status_code == 401


def test_the_portal_is_all_a_company_reaches(ctx):
    # The network's data stays out of reach.
    for method, path in (
        ("GET", "/customers"),
        ("GET", "/subscriptions"),
        ("GET", "/appointments"),
        ("GET", "/professionals"),
        ("GET", "/modalities"),
        ("GET", "/clinics"),
        ("GET", "/partners"),
        ("GET", "/agreements"),
        ("GET", f"/agreements/{ctx.terms['id']}/invoices"),
        ("POST", f"/agreements/{ctx.terms['id']}/invoices/2026-10/pay"),
        ("GET", "/regions"),
        ("GET", "/users"),
        ("GET", "/reports/plans"),
        ("GET", "/reports/finance"),
        ("GET", "/reports/commissions"),
        ("GET", "/reports/payouts"),
        ("GET", "/payouts/settlements"),
        ("GET", "/queue"),
        ("GET", "/tax-rules"),
        ("GET", "/company"),
        ("GET", "/sellers/me"),
        ("GET", "/chat/key"),
    ):
        response = ctx.client.request(method, path, headers=ctx.hr, json={"paid_on": "2026-10-20"} if method == "POST" else None)
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
    # The home of the network shows nothing of it.
    ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": ctx.client.post("/customers", headers=ctx.admin, json={"name": "B2C"}).json()["id"], "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    )
    home = ctx.client.get("/dashboard", headers=ctx.hr).json()
    assert (home["subscriptions_pending"], home["appointments_today"], home["revenue_month_cents"]) == (0, 0, None)
    assert home["agreements_in_force"] is None
    # What it does reach: its portal, its notifications, its account.
    assert ctx.client.get("/notifications", headers=ctx.hr).status_code == 200
    assert ctx.client.get("/auth/me", headers=ctx.hr).status_code == 200
    # And other roles never reach the portal.
    for role in ("admin", "attendant"):
        assert ctx.client.get("/portal", headers=auth_as(ctx.client, role)).status_code == 403


# --- home and agreements -----------------------------------------------------------------


def test_the_company_sees_its_own_agreements_only(ctx):
    home = ctx.client.get("/portal", headers=ctx.hr).json()
    assert home["partner"]["name"] == "Acme Indústria"
    assert [item["name"] for item in home["agreements"]] == ["Acme 2026/2"]
    assert (home["employees_enrolled"], home["shortfall_open_cents"], home["company_open_cents"]) == (0, 0, 0)
    own = ctx.client.get(f"/portal/agreements/{ctx.terms['id']}", headers=ctx.hr)
    assert own.status_code == 200
    assert (own.json()["min_employees"], own.json()["ends_on"], own.json()["plans"][0]["discount_percent"]) == (2, "2027-03-31", 20)
    assert [item["id"] for item in ctx.client.get("/portal/agreements", headers=ctx.hr).json()] == [ctx.terms["id"]]
    # Another company's agreement does not exist for it.
    for path in (
        f"/portal/agreements/{ctx.globex_terms['id']}",
        f"/portal/agreements/{ctx.globex_terms['id']}/invoices",
        f"/portal/agreements/{ctx.globex_terms['id']}/invoices/2026-10",
    ):
        assert ctx.client.get(path, headers=ctx.hr).status_code == 404


def test_the_company_follows_its_invoices_and_the_minimum(ctx):
    ctx.enroll(ctx.hr, ctx.terms["id"])
    invoices = ctx.client.get(f"/portal/agreements/{ctx.terms['id']}/invoices", headers=ctx.hr).json()
    october = invoices[0]
    assert (october["period"], october["employees"], october["below_minimum"], october["shortfall_cents"]) == ("2026-10", 1, True, 1980)
    assert (october["company_cents"], october["status"]) == (7920 + 1980, "open")
    detail = ctx.client.get(f"/portal/agreements/{ctx.terms['id']}/invoices/2026-10", headers=ctx.hr).json()
    assert [line["customer_name"] for line in detail["lines"]] == ["Ana Employee"]
    agreement = ctx.client.get(f"/portal/agreements/{ctx.terms['id']}", headers=ctx.hr).json()
    assert (agreement["employees"], agreement["missing_employees"], agreement["shortfall_cents"]) == (1, 1, 1980)
    home = ctx.client.get("/portal", headers=ctx.hr).json()
    assert (home["employees_enrolled"], home["company_open_cents"]) == (1, 7920 + 1980)


# --- enrolling employees ------------------------------------------------------------------------


def test_the_company_enrolls_an_employee(ctx, outbox):
    outbox.sent.clear()
    employee = ctx.enroll(ctx.hr, ctx.terms["id"], email="ana@acme.example", birth_date="1990-05-01", phone="(11) 90000-0000")
    assert (employee["name"], employee["document"], employee["agreement_name"]) == ("Ana Employee", "123.456.789-01", "Acme 2026/2")
    # From today to the end of the contract, at the frozen conditions.
    assert (employee["joined_on"], employee["expires_on"], employee["discount_percent"]) == ("2026-10-22", "2027-03-31", 20)
    assert (employee["monthly_amount_cents"], employee["list_amount_cents"], employee["installments_total"]) == (7920, 9900, 6)
    assert (employee["status"], employee["usable_from"]) == ("pendente", "2026-10-22")
    # The record is registered with what the company gave, nothing more.
    customer = ctx.client.get(f"/customers/{employee['customer_id']}", headers=ctx.admin).json()
    assert (customer["name"], customer["document"], customer["email"], customer["birth_date"]) == (
        "Ana Employee",
        "123.456.789-01",
        "ana@acme.example",
        "1990-05-01",
    )
    subscription = ctx.client.get(f"/subscriptions/{employee['subscription_id']}", headers=ctx.admin).json()
    assert (subscription["agreement_id"], subscription["paid_by_company"]) == (ctx.terms["id"], True)
    # The staff is told; the employee gets its welcome.
    staff = [row["title"] for row in ctx.client.get("/notifications", headers=ctx.admin).json() if row["event"] == "agreement.enrollment"]
    assert staff == ["Acme Indústria enrolled Ana Employee in Plan Foo"]
    ctx.client.post("/queue/run", headers=ctx.admin)
    assert "ana@acme.example" in {mail["to"] for mail in outbox.sent}
    # The company sees its employees — and no health data.
    listed = ctx.client.get("/portal/employees", headers=ctx.hr).json()
    assert [row["subscription_id"] for row in listed] == [employee["subscription_id"]]
    assert not {"allergies", "blood_type", "notes", "mother_name"} & set(listed[0])


def test_enrollment_rules_in_the_portal(ctx):
    ctx.enroll(ctx.hr, ctx.terms["id"], document="123", expect=422)  # a CPF has 11 digits
    ctx.enroll(ctx.hr, ctx.terms["id"], modality_id=ctx.other_plan["id"], expect=409)  # not covered
    ctx.enroll(ctx.hr, ctx.terms["id"], joined_on="2026-10-01", expect=409)  # no backdating
    ctx.enroll(ctx.hr, ctx.terms["id"], joined_on="2027-03-31", expect=409)  # the contract's last day: too late
    ctx.enroll(ctx.hr, ctx.globex_terms["id"], expect=404)  # another company's agreement
    suspended = ctx.agreement(ctx.acme["id"], name="Suspended", active=False)
    ctx.enroll(ctx.hr, suspended["id"], expect=409)
    ctx.enroll(ctx.hr, ctx.terms["id"])
    again = ctx.enroll(ctx.hr, ctx.terms["id"], expect=409)
    assert again["detail"] == "Ana Employee is already enrolled in Acme 2026/2 until 2027-03-31"
    later = ctx.enroll(ctx.hr, ctx.terms["id"], name="Bruno Future", document="222.333.444-55", joined_on="2026-12-01")
    assert (later["joined_on"], later["installments_total"]) == ("2026-12-01", 4)


def test_an_existing_customer_is_found_by_cpf_and_name(ctx):
    existing = ctx.client.post(
        "/customers", headers=ctx.admin, json={"name": "José da Silva", "document": "98765432100", "allergies": "Dipirona"}
    ).json()
    # A CPF alone reveals nobody: the name must match the one on file.
    wrong = ctx.enroll(ctx.hr, ctx.terms["id"], name="Someone Else", document="987.654.321-00", expect=409)
    assert "registered under another name" in wrong["detail"]
    employee = ctx.enroll(ctx.hr, ctx.terms["id"], name="jose  DA silva", document="987.654.321-00")
    assert employee["customer_id"] == existing["id"]
    assert ctx.client.get(f"/customers/{existing['id']}", headers=ctx.admin).json()["allergies"] == "Dipirona"  # untouched


def test_a_customer_with_a_personal_plan_is_moved_by_the_staff(ctx):
    holder = ctx.client.post("/customers", headers=ctx.admin, json={"name": "Carla Client", "document": "11122233344"}).json()
    ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": holder["id"], "modality_id": ctx.plan["id"], "joined_on": "2026-09-01", "expires_on": "2027-08-31"},
    )
    refused = ctx.enroll(ctx.hr, ctx.terms["id"], name="Carla Client", document="111.222.333-44", expect=409)
    assert "contact Hollic Health to move it into your agreement" in refused["detail"]


def test_the_company_enrolls_its_employees_in_the_next_contract_ahead(ctx):
    """Six-month contracts: the next one can be filled while the current runs."""
    current = ctx.enroll(ctx.hr, ctx.terms["id"])
    following = ctx.agreement(ctx.acme["id"], name="Acme 2027/1", starts_on="2027-04-01")
    renewed = ctx.enroll(ctx.hr, following["id"])
    assert renewed["customer_id"] == current["customer_id"]
    assert (renewed["joined_on"], renewed["expires_on"]) == ("2027-04-01", "2027-09-30")
    listed = ctx.client.get(f"/portal/employees?agreement_id={following['id']}", headers=ctx.hr).json()
    assert [row["subscription_id"] for row in listed] == [renewed["subscription_id"]]


# --- removing employees ----------------------------------------------------------------------


def test_the_company_removes_an_employee_who_left(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"])
    path = f"/portal/employees/{employee['subscription_id']}/cancel"
    assert ctx.client.post(path, headers=ctx.hr, json={"cancelled_on": "2026-10-01"}).status_code == 409  # never in the past
    removed = ctx.client.post(path, headers=ctx.hr, json={"cancelled_on": "2026-11-30"})
    assert removed.status_code == 200, removed.text
    assert removed.json()["withdrawn"] is False
    left = removed.json()["employee"]
    assert (left["cancelled_on"], left["status"], left["installments_total"]) == ("2026-11-30", "cancelada", 2)
    assert ctx.client.post(path, headers=ctx.hr).status_code == 409  # already cancelled
    staff = [row["title"] for row in ctx.client.get("/notifications", headers=ctx.admin).json() if row["event"] == "agreement.enrollment"]
    assert staff[0] == "Acme Indústria removed Ana Employee from Acme 2026/2"


def test_an_enrollment_not_started_is_withdrawn_altogether(ctx):
    future = ctx.enroll(ctx.hr, ctx.terms["id"], joined_on="2026-12-01")
    removed = ctx.client.post(f"/portal/employees/{future['subscription_id']}/cancel", headers=ctx.hr)
    assert removed.json() == {"withdrawn": True, "employee": None}
    assert ctx.client.get(f"/subscriptions/{future['subscription_id']}", headers=ctx.admin).status_code == 404
    assert ctx.client.get("/portal/employees", headers=ctx.hr).json() == []


def test_a_company_cannot_touch_another_companys_employees(ctx):
    globex_hr = ctx.login(ctx.globex["id"])
    theirs = ctx.enroll(globex_hr, ctx.globex_terms["id"], name="Gil Globex", document="555.666.777-88")
    assert ctx.client.post(f"/portal/employees/{theirs['subscription_id']}/cancel", headers=ctx.hr).status_code == 404
    assert ctx.client.get("/portal/employees", headers=ctx.hr).json() == []


# --- notices -------------------------------------------------------------------------------------


def test_the_company_is_told_when_its_minimum_is_reached(ctx):
    ctx.enroll(ctx.hr, ctx.terms["id"])
    ctx.enroll(ctx.hr, ctx.terms["id"], name="Bruno Employee", document="222.333.444-55")
    titles = [row["title"] for row in ctx.client.get("/notifications", headers=ctx.hr).json() if row["event"] == "agreement.conditions"]
    assert titles == ["Acme Indústria: minimum of 2 employees reached"]
    link = [row["link"] for row in ctx.client.get("/notifications", headers=ctx.hr).json() if row["event"] == "agreement.conditions"][0]
    assert link == f"/my-agreements/{ctx.terms['id']}"


def test_the_company_gets_the_receipt_of_its_invoice(ctx):
    ctx.enroll(ctx.hr, ctx.terms["id"])
    clock.travel_to(datetime(2026, 11, 3, 10, 0))
    paid = ctx.client.post(f"/agreements/{ctx.terms['id']}/invoices/2026-10/pay", headers=ctx.admin, json={"paid_on": "2026-11-03"})
    assert paid.status_code == 200, paid.text
    assert (paid.json()["company_fees_paid_cents"], paid.json()["shortfall_cents"], paid.json()["status"]) == (7920, 1980, "paid")
    receipts = [row for row in ctx.client.get("/notifications", headers=ctx.hr).json() if row["event"] == "agreement.invoice_paid"]
    assert receipts[0]["title"] == "Invoice 2026-10 paid — Acme Indústria"
    assert "shortfall" in receipts[0]["body"] and "Total R$ 99,00" in receipts[0]["body"]  # the list price, in the end


# --- editing employees and their dependents ------------------------------------------------


def employee_events(ctx, customer_id):
    headers = auth_as(ctx.client, "client", customer_id=customer_id)
    return [row for row in ctx.client.get("/notifications", headers=headers).json() if row["event"] == "agreement.employee_updated"]


def test_the_employees_list_shows_the_waiting_period(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"])
    listed = ctx.client.get("/portal/employees", headers=ctx.hr).json()[0]
    assert (listed["subscription_id"], listed["grace_days"], listed["usable_from"]) == (employee["subscription_id"], 0, "2026-10-22")
    slow = ensure_plan(ctx.client, ctx.admin, name="Plan Slow", grace_days=30)
    terms = ctx.agreement(ctx.acme["id"], name="Slow", plans=[{"modality_id": slow["id"], "discount_percent": 10}])
    other = ctx.enroll(ctx.hr, terms["id"], modality_id=slow["id"], name="Bruno Slow", document="222.333.444-55")
    assert (other["grace_days"], other["usable_from"]) == (30, "2026-11-21")


def test_the_company_edits_its_employee(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"], email="ana@acme.example")
    customer_id = employee["customer_id"]
    employee_events(ctx, customer_id)  # the employee's login exists before the change
    path = f"/portal/employees/{employee['subscription_id']}"
    detail = ctx.client.get(path, headers=ctx.hr).json()
    assert (detail["name"], detail["dependents"], detail["dependent_limit"], detail["extra_dependent_cents"]) == ("Ana Employee", [], 2, 1500)
    body = {"name": "Ana Maria Employee", "document": "123.456.789-01", "birth_date": "1990-05-01", "email": "ana.maria@acme.example", "phone": "(11) 95555-0000"}
    edited = ctx.client.put(path, headers=ctx.hr, json=body)
    assert edited.status_code == 200, edited.text
    assert (edited.json()["name"], edited.json()["email"], edited.json()["birth_date"]) == ("Ana Maria Employee", "ana.maria@acme.example", "1990-05-01")
    customer = ctx.client.get(f"/customers/{customer_id}", headers=ctx.admin).json()
    assert (customer["name"], customer["phone"]) == ("Ana Maria Employee", "(11) 95555-0000")
    notice = employee_events(ctx, customer_id)[0]
    assert notice["title"] == "Acme Indústria updated your corporate plan"
    assert "Changed: name, birth date, email, phone." in notice["body"]
    # A CPF that is someone else's, or not a CPF, is refused.
    ctx.client.post("/customers", headers=ctx.admin, json={"name": "Someone", "document": "98765432100"})
    assert ctx.client.put(path, headers=ctx.hr, json={**body, "document": "987.654.321-00"}).status_code == 409
    assert ctx.client.put(path, headers=ctx.hr, json={**body, "document": "123"}).status_code == 422


def test_the_company_moves_its_employee_to_another_plan_of_the_agreement(ctx):
    bar = ensure_plan(ctx.client, ctx.admin, name="Plan Bar", plan_value_cents=19900)
    outside = ensure_plan(ctx.client, ctx.admin, name="Plan Outside")
    terms = ctx.agreement(
        ctx.acme["id"],
        name="Two plans",
        plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}, {"modality_id": bar["id"], "discount_percent": 15}],
    )
    employee = ctx.enroll(ctx.hr, terms["id"])
    employee_events(ctx, employee["customer_id"])  # the employee's login exists before the change
    path = f"/portal/employees/{employee['subscription_id']}"
    body = {"name": "Ana Employee", "document": "123.456.789-01"}
    moved = ctx.client.put(path, headers=ctx.hr, json={**body, "modality_id": bar["id"]})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["modality_name"], moved.json()["discount_percent"], moved.json()["monthly_amount_cents"]) == ("Plan Bar", 15, 16915)
    refused = ctx.client.put(path, headers=ctx.hr, json={**body, "modality_id": outside["id"]})
    assert refused.status_code == 409 and "not covered" in refused.json()["detail"]
    assert "plan Plan Foo → Plan Bar" in employee_events(ctx, employee["customer_id"])[0]["body"]


def test_the_start_of_an_enrollment_changes_only_before_it_starts(ctx):
    later = ctx.enroll(ctx.hr, ctx.terms["id"], joined_on="2026-12-01")
    path = f"/portal/employees/{later['subscription_id']}"
    body = {"name": "Ana Employee", "document": "123.456.789-01"}
    sooner = ctx.client.put(path, headers=ctx.hr, json={**body, "joined_on": "2026-11-15"})
    assert sooner.status_code == 200, sooner.text
    assert (sooner.json()["joined_on"], sooner.json()["installments_total"]) == ("2026-11-15", 5)
    assert ctx.client.put(path, headers=ctx.hr, json={**body, "joined_on": "2026-10-01"}).status_code == 409  # not in the past
    started = ctx.enroll(ctx.hr, ctx.terms["id"], name="Bruno", document="222.333.444-55")
    response = ctx.client.put(f"/portal/employees/{started['subscription_id']}", headers=ctx.hr, json={"name": "Bruno", "document": "222.333.444-55", "joined_on": "2026-11-01"})
    assert response.status_code == 409 and "has started" in response.json()["detail"]


def test_the_company_manages_its_employees_dependents(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"])
    employee_events(ctx, employee["customer_id"])  # the employee's login exists before the changes
    base = f"/portal/employees/{employee['subscription_id']}/dependents"
    for name in ("Kid One", "Kid Two"):
        added = ctx.client.post(base, headers=ctx.hr, json={"name": name, "birth_date": "2015-03-01", "kinship": "filho(a)", "sex": "f"})
        assert added.status_code == 201, added.text
    detail = added.json()
    assert [(row["name"], row["kinship"], row["sex"]) for row in detail["dependents"]] == [("Kid One", "filho(a)", "F"), ("Kid Two", "filho(a)", "F")]
    assert (detail["dependents_count"], detail["monthly_amount_cents"]) == (2, 7920)  # two are included in the plan
    third = ctx.client.post(base, headers=ctx.hr, json={"name": "Spouse", "birth_date": "1991-01-01", "kinship": "cônjuge", "document": "333.444.555-66"})
    assert (third.json()["dependents_count"], third.json()["monthly_amount_cents"]) == (3, 9120)  # (99.00 + 15.00) - 20%
    subscription = ctx.client.get(f"/subscriptions/{employee['subscription_id']}", headers=ctx.admin).json()
    assert subscription["installments"][0]["amount_cents"] == 9120  # the open fees follow
    # Health data is never the company's: not shown, and kept when it edits.
    spouse = next(row for row in third.json()["dependents"] if row["name"] == "Spouse")
    assert set(spouse) == {"id", "name", "birth_date", "kinship", "document", "sex"}
    staff_path = f"/customers/{employee['customer_id']}/dependents/{spouse['id']}"
    record = ctx.client.get(staff_path, headers=ctx.admin).json()
    ctx.client.put(staff_path, headers=ctx.admin, json={**record, "allergies": "Dipirona", "blood_type": "O+"})
    edited = ctx.client.put(f"{base}/{spouse['id']}", headers=ctx.hr, json={"name": "Spouse Name", "birth_date": "1991-01-01", "kinship": "cônjuge"})
    assert edited.status_code == 200, edited.text
    record = ctx.client.get(staff_path, headers=ctx.admin).json()
    assert (record["name"], record["allergies"], record["blood_type"]) == ("Spouse Name", "Dipirona", "O+")
    # Removing one brings the fee back.
    removed = ctx.client.delete(f"{base}/{spouse['id']}", headers=ctx.hr)
    assert (removed.json()["dependents_count"], removed.json()["monthly_amount_cents"]) == (2, 7920)
    assert ctx.client.delete(f"{base}/{spouse['id']}", headers=ctx.hr).status_code == 404
    titles = [row["body"] for row in employee_events(ctx, employee["customer_id"])]
    assert any("Dependent removed: Spouse Name." in body for body in titles)
    # Validation: a dependent needs its birth date; a CPF has 11 digits.
    assert ctx.client.post(base, headers=ctx.hr, json={"name": "No birth"}).status_code == 422
    assert ctx.client.post(base, headers=ctx.hr, json={"name": "Bad CPF", "birth_date": "2015-01-01", "document": "12"}).status_code == 422


def test_a_dependent_with_appointments_stays(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"])
    base = f"/portal/employees/{employee['subscription_id']}/dependents"
    kid = ctx.client.post(base, headers=ctx.hr, json={"name": "Kid", "birth_date": "2015-03-01"}).json()["dependents"][0]
    ctx.client.post(f"/agreements/{ctx.terms['id']}/invoices/2026-10/pay", headers=ctx.admin, json={"paid_on": "2026-10-22"})
    professional = ctx.client.post(
        "/professionals", headers=ctx.admin, json={"name": "Lorem", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()
    booked = ctx.client.post(
        "/appointments",
        headers=ctx.admin,
        json={"customer_id": employee["customer_id"], "dependent_id": kid["id"], "professional_id": professional["id"], "marked_at": "2026-10-26T08:00"},
    )
    assert booked.status_code == 201, booked.text
    refused = ctx.client.delete(f"{base}/{kid['id']}", headers=ctx.hr)
    assert refused.status_code == 409 and "has appointments" in refused.json()["detail"]


def test_only_its_own_employees_while_enrolled(ctx):
    employee = ctx.enroll(ctx.hr, ctx.terms["id"])
    globex_hr = ctx.login(ctx.globex["id"])
    theirs = ctx.enroll(globex_hr, ctx.globex_terms["id"], name="Gil Globex", document="555.666.777-88")
    other = f"/portal/employees/{theirs['subscription_id']}"
    assert ctx.client.get(other, headers=ctx.hr).status_code == 404
    assert ctx.client.put(other, headers=ctx.hr, json={"name": "X", "document": "555.666.777-88"}).status_code == 404
    assert ctx.client.post(f"{other}/dependents", headers=ctx.hr, json={"name": "X", "birth_date": "2015-01-01"}).status_code == 404
    # Another employee's dependent is not this one's.
    mine = f"/portal/employees/{employee['subscription_id']}/dependents"
    their_kid = ctx.client.post(f"{other}/dependents", headers=globex_hr, json={"name": "Their kid", "birth_date": "2015-01-01"}).json()["dependents"][0]
    assert ctx.client.put(f"{mine}/{their_kid['id']}", headers=ctx.hr, json={"name": "Mine", "birth_date": "2015-01-01"}).status_code == 404
    # Once the employee left, it is no longer the company's to manage.
    ctx.client.post(f"/portal/employees/{employee['subscription_id']}/cancel", headers=ctx.hr)
    path = f"/portal/employees/{employee['subscription_id']}"
    assert ctx.client.put(path, headers=ctx.hr, json={"name": "Ana", "document": "123.456.789-01"}).status_code == 409
    assert ctx.client.post(f"{path}/dependents", headers=ctx.hr, json={"name": "Kid", "birth_date": "2015-01-01"}).status_code == 409
    assert ctx.client.get(path, headers=ctx.hr).status_code == 200  # still readable: its history

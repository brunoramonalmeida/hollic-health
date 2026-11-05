"""API tests for the co-payment of corporate plans: the split of each fee
between the company and the employee (the contract's default, the plan's,
the one agreed with each employee and its changes over time), the two parts
of each fee paid on their own, the company's and the employee's statements,
and the money reports."""

from datetime import date, datetime

import pytest

from app import clock
from conftest import auth, auth_as, ensure_plan

STARTS = date(2026, 10, 1)
ENDS = date(2027, 3, 31)


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.outbox = outbox
    c.plan = ensure_plan(client, admin)  # 99.00 -> 79.20 at 20% off
    c.bar = ensure_plan(client, admin, name="Plan Bar", plan_value_cents=19900)  # 199.00 -> 169.15 at 15% off
    c.partner = client.post("/partners", headers=admin, json={"name": "Umbrella Logística", "email": "rh@umbrella.example"}).json()

    def agreement(expect=201, **fields):
        body = {
            "partner_id": c.partner["id"],
            "name": "Umbrella 2026/2",
            "starts_on": STARTS.isoformat(),
            "min_employees": 1,
            "payer": "shared",
            "company_share_percent": 60,
            "plans": [
                {"modality_id": c.plan["id"], "discount_percent": 20, "grace_days": 0},
                {"modality_id": c.bar["id"], "discount_percent": 15, "grace_days": 0, "company_share_percent": 50},
            ],
            **fields,
        }
        response = client.post("/agreements", headers=admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def customer(name="Daniela", **fields):
        return client.post("/customers", headers=admin, json={"name": name, **fields}).json()["id"]

    def enroll(agreement_id, customer_id=None, plan=None, joined=STARTS, expires=ENDS, headers=None, expect=201, **fields):
        body = {
            "customer_id": customer_id or customer(),
            "modality_id": (plan or c.plan)["id"],
            "joined_on": joined.isoformat(),
            "expires_on": expires.isoformat(),
            "agreement_id": agreement_id,
            **fields,
        }
        response = client.post("/subscriptions", headers=headers or admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def get(subscription_id, headers=None):
        response = client.get(f"/subscriptions/{subscription_id}", headers=headers or admin)
        assert response.status_code == 200, response.text
        return response.json()

    def pay(subscription, index, paid_on, part=None, expect=200, **extra):
        item = subscription["installments"][index]
        body = {"paid_on": paid_on.isoformat() if paid_on else None, **extra}
        if part:
            body["part"] = part
        response = client.put(f"/subscriptions/{subscription['id']}/installments/{item['id']}", headers=admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def share(subscription_id, percent, effective_from=None, headers=None, expect=200):
        body = {"company_share_percent": percent}
        if effective_from:
            body["effective_from"] = effective_from.isoformat()
        response = client.put(f"/subscriptions/{subscription_id}/share", headers=headers or admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def pay_invoice(agreement_id, period, paid_on, expect=200):
        response = client.post(f"/agreements/{agreement_id}/invoices/{period}/pay", headers=admin, json={"paid_on": paid_on.isoformat()})
        assert response.status_code == expect, response.text
        return response.json()

    c.agreement = agreement
    c.customer = customer
    c.enroll = enroll
    c.get = get
    c.pay = pay
    c.share = share
    c.pay_invoice = pay_invoice
    return c


def parts(subscription, count=None):
    return [(item["company_share_cents"], item["employee_share_cents"]) for item in subscription["installments"]][:count]


def travel(day):
    clock.travel_to(datetime.combine(day, datetime.min.time()).replace(hour=10))


# --- the contract's split --------------------------------------------------------------------


def test_a_co_payment_contract_sets_the_default_split(ctx):
    terms = ctx.agreement()
    assert (terms["payer"], terms["company_share_percent"]) == ("shared", 60)
    assert {item["modality_name"]: item["company_share_percent"] for item in terms["plans"]} == {"Plan Bar": 50, "Plan Foo": None}
    ctx.agreement(company_share_percent=None, name="No split", expect=422)
    ctx.agreement(company_share_percent=100, name="All", expect=422)  # that is payer company
    ctx.agreement(company_share_percent=0, name="None", expect=422)  # that is payer employee
    # A plan's own split only makes sense in a co-payment.
    ctx.agreement(payer="company", company_share_percent=None, name="Company", expect=422)
    company = ctx.agreement(payer="company", name="Company", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}])
    employee = ctx.agreement(payer="employee", name="Employee", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}])
    assert (company["company_share_percent"], employee["company_share_percent"]) == (100, 0)


def test_the_default_split_is_frozen_with_the_terms(ctx):
    terms = ctx.agreement()
    ctx.enroll(terms["id"])
    body = {
        "partner_id": terms["partner_id"],
        "name": terms["name"],
        "starts_on": terms["starts_on"],
        "min_employees": 1,
        "payer": "shared",
        "company_share_percent": 70,
        "plans": [
            {"modality_id": ctx.plan["id"], "discount_percent": 20, "grace_days": 0},
            {"modality_id": ctx.bar["id"], "discount_percent": 15, "grace_days": 0, "company_share_percent": 50},
        ],
    }
    assert ctx.client.put(f"/agreements/{terms['id']}", headers=ctx.admin, json=body).status_code == 409


# --- enrollments: the split of each fee ---------------------------------------------------------


def test_each_fee_is_split_between_the_company_and_the_employee(ctx):
    terms = ctx.agreement()
    foo = ctx.enroll(terms["id"])
    assert parts(foo) == [(4752, 3168)] * 6  # 60% of 79.20
    assert (foo["payer"], foo["company_share_percent"], foo["company_monthly_cents"], foo["employee_monthly_cents"]) == ("shared", 60, 4752, 3168)
    assert [(row["company_share_percent"], row["employee_share_percent"], row["effective_from"]) for row in foo["share_history"]] == [(60, 40, "2026-10-01")]
    bar = ctx.enroll(terms["id"], plan=ctx.bar)
    assert parts(bar, 1) == [(8458, 8457)]  # the plan's 50%: the company's part rounds half up
    # The split the company and the employee agreed.
    agreed = ctx.enroll(terms["id"], company_share_percent=80)
    assert parts(agreed, 1) == [(6336, 1584)]


def test_a_split_only_where_the_contract_allows_it(ctx):
    company = ctx.agreement(payer="company", company_share_percent=None, name="Company", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}])
    paid_by_company = ctx.enroll(company["id"])
    assert parts(paid_by_company, 1) == [(7920, 0)]
    ctx.enroll(company["id"], company_share_percent=50, expect=409)
    ctx.share(paid_by_company["id"], 50, expect=409)
    employee = ctx.agreement(payer="employee", company_share_percent=None, name="Employee", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}])
    assert parts(ctx.enroll(employee["id"]), 1) == [(0, 7920)]
    # An individual plan has no split, and only the staff sets one.
    individual = ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": ctx.customer("B2C"), "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30", "company_share_percent": 50},
    )
    assert individual.status_code == 409
    holder = ctx.customer("Self")
    client = auth_as(ctx.client, "client", customer_id=holder)
    refused = ctx.client.post(
        "/subscriptions",
        headers=client,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-03-31", "company_share_percent": 90},
    )
    assert refused.status_code == 403


# --- paying the two parts ------------------------------------------------------------------------


def test_the_fee_is_paid_once_both_parts_are_in(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    employee = auth_as(ctx.client, "client", customer_id=enrolled["customer_id"])
    # The employee pays its part first: the fee waits for the company's.
    after = ctx.pay(enrolled, 0, date(2026, 10, 3), part="employee")
    first = after["installments"][0]
    assert (first["employee_paid_on"], first["employee_paid_cents"], first["paid_on"], first["status"]) == ("2026-10-03", 3168, None, "overdue")
    assert after["status"] == "pendente"
    events = [row["event"] for row in ctx.client.get("/notifications", headers=employee).json()]
    assert "installment.part_paid" in events and "installment.paid" not in events
    # The company pays its part on its invoice: the fee is paid, the plan active.
    invoice = ctx.pay_invoice(terms["id"], "2026-10", date(2026, 10, 5))
    assert (invoice["company_share_cents"], invoice["company_fees_paid_cents"], invoice["employee_share_cents"], invoice["employee_paid_cents"]) == (4752, 4752, 3168, 3168)
    assert invoice["status"] == "paid"
    after = ctx.get(enrolled["id"])
    first = after["installments"][0]
    assert (first["company_paid_on"], first["paid_on"], first["paid_cents"], first["status"]) == ("2026-10-05", "2026-10-05", 7920, "paid")
    assert after["status"] == "ativa"
    assert "installment.paid" in [row["event"] for row in ctx.client.get("/notifications", headers=employee).json()]


def test_the_company_invoice_carries_only_its_part(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    ctx.enroll(terms["id"], company_share_percent=80)
    invoice = ctx.pay_invoice(terms["id"], "2026-10", date(2026, 10, 5))
    assert (invoice["amount_cents"], invoice["company_share_cents"], invoice["employee_share_cents"]) == (15840, 4752 + 6336, 3168 + 1584)
    assert (invoice["company_cents"], invoice["company_open_cents"], invoice["employee_open_cents"]) == (11088, 0, 4752)
    # The company paid its part; the employees' stay theirs: their fees are not paid yet.
    assert invoice["status"] == "paid"
    assert all(line["company_paid_on"] == "2026-10-05" and line["employee_paid_on"] is None for line in invoice["lines"])
    assert ctx.get(enrolled["id"])["installments"][0]["status"] == "overdue"


def test_parts_registered_together_undone_and_taxed_on_settlement(ctx):
    response = ctx.client.post(
        "/tax-rules",
        headers=ctx.admin,
        json={"code": "ISS", "name": "ISS", "level": "municipal", "rate_bp": 500, "applies_to": "subscription", "effective_from": "2026-01-01"},
    )
    assert response.status_code == 201
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    ctx.pay(enrolled, 0, date(2026, 10, 5), part="company")
    assert ctx.get(enrolled["id"])["installments"][0]["tax_cents"] == 0  # not settled: no taxes yet
    # Both parts at once (omitted part): the open one is registered.
    settled = ctx.pay(enrolled, 0, date(2026, 10, 6))["installments"][0]
    assert (settled["paid_on"], settled["company_paid_on"], settled["employee_paid_on"], settled["tax_cents"]) == ("2026-10-06", "2026-10-05", "2026-10-06", 396)
    # Undoing a part reopens the fee and drops its taxes.
    undone = ctx.pay(enrolled, 0, None, part="employee")["installments"][0]
    assert (undone["paid_on"], undone["company_paid_on"], undone["employee_paid_on"], undone["tax_cents"]) == (None, "2026-10-05", None, 0)
    # An amount goes with the part it pays: with both parts open, it must say which.
    ctx.pay(enrolled, 1, date(2026, 10, 6), paid_cents=100, expect=400)
    assert ctx.pay(enrolled, 0, date(2026, 10, 6), part="employee", paid_cents=3000)["installments"][0]["paid_cents"] == 4752 + 3000
    # An individual plan is paid in one go.
    individual = ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": ctx.customer("B2C"), "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    ).json()
    ctx.pay(individual, 0, date(2026, 10, 3), part="employee", expect=400)


def test_a_fee_with_a_part_paid_is_never_dropped_nor_re_split(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    ctx.pay(enrolled, 1, date(2026, 10, 20), part="company")  # November's company part, in advance
    ctx.share(enrolled["id"], 30, date(2026, 11, 1))
    assert parts(ctx.get(enrolled["id"]), 3) == [(4752, 3168), (4752, 3168), (2376, 5544)]
    cancelled = ctx.client.post(f"/subscriptions/{enrolled['id']}/cancel", headers=ctx.admin, json={"cancelled_on": "2026-10-22"})
    assert [item["sequence"] for item in cancelled.json()["installments"]] == [1, 2]  # November kept: its company part is paid


# --- the split changes over time ----------------------------------------------------------------


def test_the_split_changes_from_a_date_on(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    employee = auth_as(ctx.client, "client", customer_id=enrolled["customer_id"])
    ctx.pay_invoice(terms["id"], "2026-10", date(2026, 10, 5))  # October's company part is paid
    changed = ctx.share(enrolled["id"], 40, date(2026, 12, 1))
    assert parts(changed) == [(4752, 3168), (4752, 3168)] + [(3168, 4752)] * 4
    assert (changed["company_share_percent"], changed["employee_monthly_cents"]) == (60, 3168)  # still 60% today
    history = [(row["company_share_percent"], row["effective_from"]) for row in changed["share_history"]]
    assert history == [(60, "2026-10-01"), (40, "2026-12-01")]
    # A change from the start re-splits every open fee but October (a part is paid).
    changed = ctx.share(enrolled["id"], 90, date(2026, 10, 1))
    assert parts(changed, 3) == [(4752, 3168), (7128, 792), (3168, 4752)]
    notices = [row for row in ctx.client.get("/notifications", headers=employee).json() if row["event"] == "agreement.share_changed"]
    assert notices[0]["title"] == "Your co-payment: you pay 10% from 01/10/2026"
    # Not past the enrollment.
    ctx.share(enrolled["id"], 50, date(2027, 4, 1), expect=409)


def test_a_new_split_for_every_employee(ctx):
    terms = ctx.agreement()
    one = ctx.enroll(terms["id"])
    two = ctx.enroll(terms["id"], plan=ctx.bar)
    gone = ctx.enroll(terms["id"], expires=date(2026, 11, 30))
    response = ctx.client.put(
        f"/agreements/{terms['id']}/share", headers=ctx.admin, json={"company_share_percent": 100, "effective_from": "2027-01-01"}
    )
    assert response.json() == {"changed": 2, "effective_from": "2027-01-01", "company_share_percent": 100}
    assert parts(ctx.get(one["id"]))[3] == (7920, 0)
    assert parts(ctx.get(two["id"]))[3] == (16915, 0)
    assert parts(ctx.get(gone["id"])) == [(4752, 3168), (4752, 3168)]
    attendant = auth_as(ctx.client, "attendant")
    assert ctx.client.put(f"/agreements/{terms['id']}/share", headers=attendant, json={"company_share_percent": 50}).status_code == 200


def test_upgrades_and_renewals_keep_the_agreed_split(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"], company_share_percent=75)
    upgraded = ctx.enroll(terms["id"], customer_id=enrolled["customer_id"], plan=ctx.bar, joined=date(2026, 12, 1), upgrade=True)
    assert upgraded["company_share_percent"] == 75
    renewed = ctx.client.post(f"/agreements/{terms['id']}/renew", headers=ctx.admin).json()
    assert (renewed["payer"], renewed["company_share_percent"]) == ("shared", 60)
    carried = ctx.client.get(f"/subscriptions?agreement_id={renewed['id']}", headers=ctx.admin).json()
    assert [row["company_share_percent"] for row in carried] == [75]


# --- the company portal -----------------------------------------------------------------------------


@pytest.fixture()
def portal(ctx):
    ctx.terms = ctx.agreement()
    ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "HR", "email": "hr@umbrella.example", "role": "partner", "password": "saude123", "partner_id": ctx.partner["id"]},
    )
    ctx.hr = auth(ctx.client, "hr@umbrella.example")
    return ctx


def portal_enroll(ctx, name, cpf, **fields):
    body = {"agreement_id": ctx.terms["id"], "modality_id": ctx.plan["id"], "name": name, "document": cpf, "joined_on": "2026-10-22", **fields}
    response = ctx.client.post("/portal/employees", headers=ctx.hr, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_the_company_agrees_the_split_with_each_employee(portal):
    ctx = portal
    ana = portal_enroll(ctx, "Ana", "111.111.111-11", company_share_percent=70)
    assert (ana["payer"], ana["company_share_percent"], ana["company_monthly_cents"], ana["employee_monthly_cents"]) == ("shared", 70, 5544, 2376)
    path = f"/portal/employees/{ana['subscription_id']}/share"
    assert ctx.client.put(path, headers=ctx.hr, json={"company_share_percent": 50, "effective_from": "2026-10-01"}).status_code == 409  # from today on
    changed = ctx.client.put(path, headers=ctx.hr, json={"company_share_percent": 50, "effective_from": "2026-12-01", "note": "new policy"})
    assert changed.status_code == 200, changed.text
    assert [(row["company_share_percent"], row["note"]) for row in changed.json()["share_history"]] == [(70, "enrollment"), (50, "new policy")]
    staff = [row["title"] for row in ctx.client.get("/notifications", headers=ctx.admin).json() if row["event"] == "agreement.share_changed"]
    assert staff == ["Umbrella Logística changed Ana's split: company 50%"]
    bulk = ctx.client.put(f"/portal/agreements/{ctx.terms['id']}/share", headers=ctx.hr, json={"company_share_percent": 65, "effective_from": "2027-01-01"})
    assert bulk.json()["changed"] == 1
    # Another company's employee does not exist for it.
    other = ctx.client.post("/partners", headers=ctx.admin, json={"name": "Other Co"}).json()
    other_terms = ctx.client.post(
        "/agreements",
        headers=ctx.admin,
        json={"partner_id": other["id"], "name": "Other", "starts_on": "2026-10-01", "min_employees": 1, "payer": "shared", "company_share_percent": 50, "plans": [{"modality_id": ctx.plan["id"]}]},
    ).json()
    theirs = ctx.enroll(other_terms["id"])
    assert ctx.client.put(f"/portal/employees/{theirs['id']}/share", headers=ctx.hr, json={"company_share_percent": 10}).status_code == 404
    assert ctx.client.put(f"/portal/agreements/{other_terms['id']}/share", headers=ctx.hr, json={"company_share_percent": 10}).status_code == 404


def test_the_company_statement(portal):
    ctx = portal
    ana = portal_enroll(ctx, "Ana", "111.111.111-11")  # 60%: 4752 / 3168
    portal_enroll(ctx, "Bruno", "222.222.222-22", company_share_percent=80)  # 6336 / 1584
    ctx.pay_invoice(ctx.terms["id"], "2026-10", date(2026, 10, 22))
    ana_sub = ctx.get(ana["subscription_id"])
    ctx.pay(ana_sub, 0, date(2026, 10, 22), part="employee")
    statement = ctx.client.get("/portal/statement?start=2026-10&end=2026-11", headers=ctx.hr).json()
    assert (statement["partner_name"], statement["start"], statement["end"]) == ("Umbrella Logística", "2026-10", "2026-11")
    october, november = statement["months"]
    assert (october["period"], october["employees"], october["fees_cents"]) == ("2026-10", 2, 15840)
    assert (october["company_share_cents"], october["employee_share_cents"]) == (11088, 4752)
    assert (october["company_paid_cents"], october["company_open_cents"]) == (11088, 0)
    assert (october["employee_paid_cents"], october["employee_open_cents"]) == (3168, 1584)
    assert (november["company_paid_cents"], november["company_open_cents"]) == (0, 11088)
    totals = statement["totals"]
    assert (totals["employees"], totals["company_share_cents"], totals["employee_share_cents"]) == (2, 22176, 9504)
    rows = {row["name"]: row for row in statement["employees"]}
    assert (rows["Ana"]["company_share_percent"], rows["Ana"]["months"], rows["Ana"]["employee_paid_cents"], rows["Ana"]["employee_open_cents"]) == (60, 2, 3168, 3168)
    assert (rows["Bruno"]["company_share_cents"], rows["Bruno"]["employee_share_cents"]) == (12672, 3168)
    # The payroll list.
    csv = ctx.client.get("/portal/statement.csv?start=2026-10&end=2026-10", headers=ctx.hr)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("month;agreement;employee;cpf;plan")
    assert "2026-10;Umbrella 2026/2;Ana;111.111.111-11;Plan Foo;1;2026-10-22;7920;60;4752;2026-10-22;3168;2026-10-22" in lines
    assert "2026-10;Umbrella 2026/2;Bruno;222.222.222-22;Plan Foo;1;2026-10-22;7920;80;6336;2026-10-22;1584;" in lines
    # The staff reads the same statement.
    staff = ctx.client.get(f"/partners/{ctx.partner['id']}/statement?start=2026-10&end=2026-11", headers=auth_as(ctx.client, "attendant"))
    assert staff.json()["totals"] == totals
    assert ctx.client.get("/portal/statement?start=2026-13&end=2026-11", headers=ctx.hr).status_code == 400


# --- the employee's statement --------------------------------------------------------------------------


def test_the_employee_checks_its_payments(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    employee = auth_as(ctx.client, "client", customer_id=enrolled["customer_id"])
    ctx.pay_invoice(terms["id"], "2026-10", date(2026, 10, 5))
    ctx.pay(ctx.get(enrolled["id"]), 0, date(2026, 10, 6), part="employee")
    travel(date(2026, 11, 5))  # November's fee is overdue now
    statement = ctx.client.get("/reports/employee-statement?start=2026-10&end=2026-12", headers=employee)
    assert statement.status_code == 200, statement.text
    data = statement.json()
    assert [(line["due_on"], line["own_cents"], line["company_cents"], line["own_status"]) for line in data["lines"]] == [
        ("2026-10-01", 3168, 4752, "paid"),
        ("2026-11-01", 3168, 4752, "overdue"),
        ("2026-12-01", 3168, 4752, "open"),
    ]
    assert data["lines"][0]["partner_name"] == "Umbrella Logística"
    assert data["totals"] == {
        "fees_cents": 23760,
        "company_cents": 14256,
        "own_cents": 9504,
        "own_paid_cents": 3168,
        "own_open_cents": 6336,
        "own_overdue_cents": 3168,
    }
    # Its own only; the staff reads anyone's; companies and sellers do not.
    other = ctx.customer("Other")
    assert ctx.client.get(f"/reports/employee-statement?customer_id={other}", headers=employee).status_code == 403
    assert ctx.client.get(f"/reports/employee-statement?customer_id={enrolled['customer_id']}", headers=auth_as(ctx.client, "attendant")).status_code == 200
    assert ctx.client.get("/reports/employee-statement", headers=ctx.admin).status_code == 400


def test_an_individual_plan_is_all_the_customers_own(ctx):
    holder = ctx.customer("B2C")
    individual = ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    ).json()
    ctx.pay(individual, 0, date(2026, 10, 2))
    data = ctx.client.get("/reports/employee-statement?start=2026-10&end=2026-10", headers=auth_as(ctx.client, "client", customer_id=holder)).json()
    assert [(line["own_cents"], line["company_cents"], line["own_status"], line["partner_name"]) for line in data["lines"]] == [(9900, 0, "paid", None)]
    # A company-paid plan leaves the employee nothing to pay.
    company = ctx.agreement(payer="company", company_share_percent=None, name="Company", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}])
    enrolled = ctx.enroll(company["id"])
    data = ctx.client.get(
        "/reports/employee-statement?start=2026-10&end=2026-10", headers=auth_as(ctx.client, "client", customer_id=enrolled["customer_id"])
    ).json()
    assert [(line["own_cents"], line["company_cents"], line["own_status"]) for line in data["lines"]] == [(0, 7920, "none")]


# --- money reports -----------------------------------------------------------------------------------


def test_reports_count_each_part_when_it_is_received(ctx):
    terms = ctx.agreement()
    enrolled = ctx.enroll(terms["id"])
    ctx.pay_invoice(terms["id"], "2026-10", date(2026, 10, 20))  # the company's 47.52 in October
    travel(date(2026, 11, 3))
    ctx.pay(ctx.get(enrolled["id"]), 0, date(2026, 11, 2), part="employee")  # the employee's 31.68 in November
    finance = ctx.client.get("/reports/finance?granularity=month&start=2026-10-01&end=2026-11-30", headers=ctx.admin).json()
    october, november = finance["periods"]
    assert (october["plan_payments"], october["plan_revenue_cents"]) == (1, 4752)
    assert (november["plan_payments"], november["plan_revenue_cents"]) == (1, 3168)
    report = ctx.client.get("/reports/plans?granularity=month&start=2026-10-01&end=2026-11-30", headers=ctx.admin).json()
    row = report["by_agreement"][0]
    assert (row["revenue_cents"], row["company_paid_cents"], row["employee_paid_cents"]) == (7920, 4752, 3168)
    home = ctx.client.get("/dashboard", headers=ctx.admin).json()
    assert home["plan_revenue_month_cents"] == 3168  # November: the employee's part

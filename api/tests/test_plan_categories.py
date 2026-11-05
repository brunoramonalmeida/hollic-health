"""API tests for plan categories and enterprise plans: the categories, the
enterprise plans only available through company agreements (never sold to
individuals nor by sellers), the category's waiting period and the report."""

from datetime import date

import pytest

from conftest import auth, auth_as, ensure_plan

STARTS = date(2026, 10, 1)
ENDS = date(2027, 3, 31)


@pytest.fixture()
def ctx(client):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin

    def category(name="Enterprise", expect=201, headers=None, **fields):
        response = client.post("/plan-categories", headers=headers or admin, json={"name": name, **fields})
        assert response.status_code == expect, response.text
        return response.json()

    def plan(name, category_id=None, expect=201, **fields):
        body = {"name": name, "plan_value_cents": 14900, "dependent_limit": 2, "grace_days": 30, "discount_percent": 40, "category_id": category_id, **fields}
        response = client.post("/modalities", headers=admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def update_plan(row, expect=200, **fields):
        body = {k: row[k] for k in ("name", "plan_value_cents", "dependent_limit", "grace_days", "extra_dependent_cents", "discount_percent", "appointment_limit", "appointment_limit_period", "category_id")}
        response = client.put(f"/modalities/{row['id']}", headers=admin, json={**body, **fields})
        assert response.status_code == expect, response.text
        return response.json()

    def customer(name="Holder", **fields):
        return client.post("/customers", headers=admin, json={"name": name, **fields}).json()["id"]

    def subscribe(plan_row, customer_id=None, headers=None, expect=201, **fields):
        body = {
            "customer_id": customer_id or customer(),
            "modality_id": plan_row["id"],
            "joined_on": STARTS.isoformat(),
            "expires_on": ENDS.isoformat(),
            **fields,
        }
        response = client.post("/subscriptions", headers=headers or admin, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def agreement(plans, **fields):
        partner = client.post("/partners", headers=admin, json={"name": fields.pop("partner", "Acme")}).json()
        body = {"partner_id": partner["id"], "name": "Acme 2026/2", "starts_on": STARTS.isoformat(), "min_employees": 1, "payer": "company", "plans": plans, **fields}
        response = client.post("/agreements", headers=admin, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    c.category = category
    c.plan = plan
    c.update_plan = update_plan
    c.customer = customer
    c.subscribe = subscribe
    c.agreement = agreement
    return c


# --- categories ---------------------------------------------------------------------------


def test_categories_are_managed_by_pricing_roles_and_read_by_the_network(ctx):
    enterprise = ctx.category(enterprise=True, grace_days=0, description="Only through company agreements")
    assert (enterprise["name"], enterprise["enterprise"], enterprise["grace_days"], enterprise["plans_count"]) == ("Enterprise", True, 0, 0)
    individual = ctx.category("Individual", headers=auth_as(ctx.client, "supervisor"))
    assert (individual["enterprise"], individual["grace_days"]) == (False, None)
    ctx.category("enterprise", expect=409)  # names are unique
    ctx.category("  ", expect=422)
    ctx.category("Bad grace", grace_days=-1, expect=422)
    attendant = auth_as(ctx.client, "attendant")
    ctx.category("By attendant", headers=attendant, expect=403)
    assert [row["name"] for row in ctx.client.get("/plan-categories", headers=attendant).json()] == ["Enterprise", "Individual"]
    holder = ctx.customer()
    assert ctx.client.get("/plan-categories", headers=auth_as(ctx.client, "client", customer_id=holder)).status_code == 200
    # A category with plans stays.
    ctx.plan("Plan Corp", enterprise["id"])
    assert ctx.client.delete(f"/plan-categories/{enterprise['id']}", headers=ctx.admin).status_code == 409
    assert ctx.client.delete(f"/plan-categories/{individual['id']}", headers=ctx.admin).status_code == 204


def test_a_plan_carries_its_category(ctx):
    enterprise = ctx.category(enterprise=True, grace_days=7)
    corp = ctx.plan("Plan Corp", enterprise["id"])
    assert (corp["category_id"], corp["category_name"], corp["enterprise"]) == (enterprise["id"], "Enterprise", True)
    assert (corp["grace_days"], corp["effective_grace_days"]) == (30, 7)  # the category's waiting period wins
    plain = ctx.plan("Plan Plain")
    assert (plain["category_id"], plain["category_name"], plain["enterprise"], plain["effective_grace_days"]) == (None, None, False, 30)
    ctx.plan("Plan Ghost", 999, expect=404)
    listed = {row["name"]: row for row in ctx.client.get("/modalities", headers=ctx.admin).json()}
    assert listed["Plan Corp"]["enterprise"] is True


# --- enterprise plans: only through company agreements --------------------------------------


def test_an_enterprise_plan_is_never_sold_to_an_individual(ctx):
    corp = ctx.plan("Plan Corp", ctx.category(enterprise=True)["id"])
    refused = ctx.subscribe(corp, expect=409)
    assert refused["detail"] == '"Plan Corp" is an enterprise plan: it is only available through a company agreement'
    # Not by a client on its own either, nor as an upgrade of its plan.
    holder = ctx.customer()
    client = auth_as(ctx.client, "client", customer_id=holder)
    ctx.subscribe(corp, customer_id=holder, headers=client, expect=409)
    ctx.subscribe(ensure_plan(ctx.client, ctx.admin), customer_id=holder)
    ctx.subscribe(corp, customer_id=holder, headers=client, expect=409, joined_on="2026-12-01", upgrade=True)
    quote = ctx.client.get(f"/subscriptions/quote?customer_id={holder}&modality_id={corp['id']}", headers=ctx.admin).json()
    assert quote["allowed"] is False and "enterprise plan" in quote["reason"]


def test_an_enterprise_plan_through_a_company_agreement(ctx):
    corp = ctx.plan("Plan Corp", ctx.category(enterprise=True)["id"])
    terms = ctx.agreement([{"modality_id": corp["id"], "discount_percent": 10}])
    enrolled = ctx.subscribe(corp, agreement_id=terms["id"])
    assert (enrolled["modality_name"], enrolled["discount_percent"], enrolled["monthly_amount_cents"]) == ("Plan Corp", 10, 13410)
    # It cannot leave the agreement while on the enterprise plan…
    body = {k: enrolled[k] for k in ("customer_id", "modality_id", "joined_on", "expires_on")}
    out = ctx.client.put(f"/subscriptions/{enrolled['id']}", headers=ctx.admin, json={**body, "agreement_id": None})
    assert out.status_code == 409
    # …but the employee may move to an individual plan (e.g. it left the company).
    personal = ensure_plan(ctx.client, ctx.admin)
    moved = ctx.subscribe(personal, customer_id=enrolled["customer_id"], joined_on="2026-12-01", expires_on="2027-11-30", upgrade=True)
    assert moved["agreement_id"] is None


def test_sellers_never_sell_an_enterprise_plan(ctx):
    corp = ctx.plan("Plan Corp", ctx.category(enterprise=True)["id"])
    response = ctx.client.post(
        "/regions", headers=ctx.admin, json={"name": "SP", "areas": [{"state": "SP"}], "plans": [{"modality_id": corp["id"]}]}
    )
    assert response.status_code == 409 and "enterprise plan" in response.json()["detail"]
    # A seller's sale of it is refused whatever its region offers.
    plain = ensure_plan(ctx.client, ctx.admin)
    region = ctx.client.post(
        "/regions", headers=ctx.admin, json={"name": "SP", "areas": [{"state": "SP"}], "plans": [{"modality_id": plain["id"]}]}
    ).json()
    seller = ctx.client.post("/sellers", headers=ctx.admin, json={"name": "Ana", "region_id": region["id"]}).json()
    holder = ctx.customer(city="Campinas", state="SP")
    sale = ctx.subscribe(corp, customer_id=holder, seller_id=seller["id"], expect=409)
    assert "enterprise plan" in sale["detail"]
    quote = ctx.client.get(
        f"/subscriptions/quote?customer_id={holder}&modality_id={corp['id']}", headers=auth_as(ctx.client, "seller", seller_id=seller["id"])
    ).json()
    assert quote["allowed"] is False


def test_a_plan_in_individual_use_cannot_become_enterprise(ctx):
    enterprise = ctx.category(enterprise=True)
    plain = ctx.plan("Plan Plain")
    sold = ctx.subscribe(plain)
    refused = ctx.update_plan(plain, category_id=enterprise["id"], expect=409)
    assert "1 individual subscription(s) in force" in refused["detail"]
    region = ctx.client.post(
        "/regions", headers=ctx.admin, json={"name": "SP", "areas": [{"state": "SP"}], "plans": [{"modality_id": plain["id"]}]}
    ).json()
    ctx.client.post(f"/subscriptions/{sold['id']}/cancel", headers=ctx.admin)
    refused = ctx.update_plan(plain, category_id=enterprise["id"], expect=409)
    assert "1 sales region(s) offering it" in refused["detail"]
    ctx.client.put(f"/regions/{region['id']}", headers=ctx.admin, json={"name": "SP", "areas": [{"state": "SP"}], "plans": []})
    # Corporate enrollments are no obstacle.
    terms = ctx.agreement([{"modality_id": plain["id"]}])
    ctx.subscribe(plain, agreement_id=terms["id"])
    assert ctx.update_plan(plain, category_id=enterprise["id"])["enterprise"] is True


def test_a_category_with_plans_in_individual_use_cannot_become_enterprise(ctx):
    family = ctx.category("Family")
    plain = ctx.plan("Plan Family", family["id"])
    ctx.subscribe(plain)
    response = ctx.client.put(f"/plan-categories/{family['id']}", headers=ctx.admin, json={"name": "Family", "enterprise": True})
    assert response.status_code == 409 and 'The plans of category "Family"' in response.json()["detail"]
    renamed = ctx.client.put(f"/plan-categories/{family['id']}", headers=ctx.admin, json={"name": "Families", "grace_days": 10})
    assert (renamed.json()["name"], renamed.json()["grace_days"]) == ("Families", 10)


# --- the category's waiting period -------------------------------------------------------------


def test_the_waiting_period_comes_from_the_contract_then_the_category_then_the_plan(ctx):
    category = ctx.category("Short wait", grace_days=7)
    plan = ctx.plan("Plan Seven", category["id"])  # its own 30 days are replaced by the category's 7
    individual = ctx.subscribe(plan)
    assert individual["usable_from"] == "2026-10-08"
    waived = ctx.agreement([{"modality_id": plan["id"], "grace_days": 0}], partner="Waived Co")
    own = ctx.agreement([{"modality_id": plan["id"]}], partner="Own Co")
    assert ctx.subscribe(plan, agreement_id=waived["id"])["usable_from"] == "2026-10-01"  # the contract's
    assert ctx.subscribe(plan, agreement_id=own["id"])["usable_from"] == "2026-10-08"  # the category's
    assert own["plans"][0]["plan_grace_days"] == 7
    # A category without a waiting period leaves each plan's.
    ctx.client.put(f"/plan-categories/{category['id']}", headers=ctx.admin, json={"name": "Short wait", "grace_days": None})
    assert ctx.client.get(f"/subscriptions/{individual['id']}", headers=ctx.admin).json()["usable_from"] == "2026-10-31"


def test_booking_follows_the_category_waiting_period(ctx):
    category = ctx.category("Short wait", grace_days=7)
    plan = ctx.plan("Plan Seven", category["id"])
    professional = ctx.client.post(
        "/professionals", headers=ctx.admin, json={"name": "Lorem", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()
    subscription = ctx.subscribe(plan, joined_on="2026-10-20", expires_on="2027-03-31")
    first = subscription["installments"][0]
    ctx.client.put(f"/subscriptions/{subscription['id']}/installments/{first['id']}", headers=ctx.admin, json={"paid_on": "2026-10-20"})
    booking = {"customer_id": subscription["customer_id"], "professional_id": professional["id"], "marked_at": "2026-10-26T08:00"}
    refused = ctx.client.post("/appointments", headers=ctx.admin, json=booking)
    assert refused.status_code == 409 and "waiting period ends on 2026-10-27" in refused.json()["detail"]
    ctx.client.put(f"/plan-categories/{category['id']}", headers=ctx.admin, json={"name": "Short wait", "grace_days": 0})
    assert ctx.client.post("/appointments", headers=ctx.admin, json=booking).status_code == 201


def test_the_company_enrolls_its_employees_in_an_enterprise_plan(ctx):
    corp = ctx.plan("Plan Corp", ctx.category(enterprise=True, grace_days=0)["id"])
    terms = ctx.agreement([{"modality_id": corp["id"], "discount_percent": 10}])
    ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "HR", "email": "hr@acme.example", "role": "partner", "password": "saude123", "partner_id": terms["partner_id"]},
    )
    hr = auth(ctx.client, "hr@acme.example")
    employee = ctx.client.post(
        "/portal/employees",
        headers=hr,
        json={"agreement_id": terms["id"], "modality_id": corp["id"], "name": "Ana", "document": "111.111.111-11"},
    )
    assert employee.status_code == 201, employee.text
    assert (employee.json()["modality_name"], employee.json()["usable_from"]) == ("Plan Corp", "2026-10-22")


# --- the report -------------------------------------------------------------------------------------


def test_plans_report_by_category(ctx):
    enterprise = ctx.category(enterprise=True)
    individual = ctx.category("Individual")
    corp = ctx.plan("Plan Corp", enterprise["id"])
    plain = ctx.plan("Plan Plain", individual["id"], grace_days=0)
    loose = ctx.plan("Plan Loose", grace_days=0)
    terms = ctx.agreement([{"modality_id": corp["id"]}])
    ctx.subscribe(corp, agreement_id=terms["id"])
    ctx.subscribe(plain)
    ctx.subscribe(plain)
    ctx.subscribe(loose)
    report = ctx.client.get("/reports/plans?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    plans = {row["name"]: row for row in report["by_plan"]}
    assert (plans["Plan Corp"]["category_name"], plans["Plan Corp"]["enterprise"]) == ("Enterprise", True)
    categories = {row["name"]: row for row in report["by_category"]}
    assert (categories["Enterprise"]["enterprise"], categories["Enterprise"]["sold"], categories["Enterprise"]["plans"]) == (True, 1, 1)
    assert (categories["Individual"]["sold"], categories["No category"]["sold"]) == (2, 1)

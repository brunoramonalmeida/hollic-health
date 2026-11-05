"""API tests for corporate partnerships (B2B), the staff side: partner
companies, six-month agreements with frozen terms, employees' enrollments
at the negotiated conditions, the minimum of employees and the shortfall the
company pays, the company's invoices, renewals and who may do what. The
companies' own portal is tested in test_portal.py."""

from datetime import date, datetime

import pytest

from app import clock
from conftest import auth, auth_as, ensure_plan

STARTS = date(2026, 10, 1)
ENDS = date(2027, 3, 31)  # six months


@pytest.fixture()
def ctx(client, outbox):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.outbox = outbox
    c.plan = ensure_plan(client, headers)  # 99.00/month, 2 dependents included, 15.00 each extra, no waiting period
    c.other_plan = ensure_plan(client, headers, name="Plan Bar", plan_value_cents=19900)

    def partner(name="Acme Indústria", expect=201, headers=None, **fields):
        body = {"name": name, "cnpj": "12.345.678/0001-90", "email": "rh@acme.example", "contact_name": "Rita RH", **fields}
        response = client.post("/partners", headers=headers or c.headers, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def agreement_body(partner_id, **fields):
        return {
            "partner_id": partner_id,
            "name": "Acme 2026/2",
            "starts_on": STARTS.isoformat(),
            "min_employees": 2,
            "payer": "company",
            "plans": [{"modality_id": c.plan["id"], "discount_percent": 20, "grace_days": 0}],
            **fields,
        }

    def agreement(partner_id=None, expect=201, headers=None, **fields):
        partner_id = partner_id or partner()["id"]
        response = client.post("/agreements", headers=headers or c.headers, json=agreement_body(partner_id, **fields))
        assert response.status_code == expect, response.text
        return response.json()

    def update_agreement(row, expect=200, headers=None, **fields):
        body = {
            "partner_id": row["partner_id"],
            "name": row["name"],
            "starts_on": row["starts_on"],
            "min_employees": row["min_employees"],
            "payer": row["payer"],
            "active": row["active"],
            "notes": row["notes"],
            "plans": [
                {"modality_id": item["modality_id"], "discount_percent": item["discount_percent"], "grace_days": item["grace_days"]}
                for item in row["plans"]
            ],
            **fields,
        }
        response = client.put(f"/agreements/{row['id']}", headers=headers or c.headers, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def customer(name="Employee", **fields):
        response = client.post("/customers", headers=c.headers, json={"name": name, **fields})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def enroll(agreement_id, customer_id=None, plan=None, joined=STARTS, expires=ENDS, headers=None, expect=201, **fields):
        body = {
            "customer_id": customer_id or customer(),
            "modality_id": (plan or c.plan)["id"],
            "joined_on": joined.isoformat(),
            "expires_on": expires.isoformat(),
            "agreement_id": agreement_id,
            **fields,
        }
        response = client.post("/subscriptions", headers=headers or c.headers, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def get(subscription_id, headers=None):
        response = client.get(f"/subscriptions/{subscription_id}", headers=headers or c.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def invoice(agreement_id, period, headers=None):
        response = client.get(f"/agreements/{agreement_id}/invoices/{period}", headers=headers or c.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def pay(agreement_id, period, paid_on, expect=200, headers=None):
        response = client.post(
            f"/agreements/{agreement_id}/invoices/{period}/pay", headers=headers or c.headers, json={"paid_on": paid_on.isoformat()}
        )
        assert response.status_code == expect, response.text
        return response.json()

    c.partner = partner
    c.agreement = agreement
    c.update_agreement = update_agreement
    c.customer = customer
    c.enroll = enroll
    c.get = get
    c.invoice = invoice
    c.pay = pay
    return c


def amounts(subscription, count=None):
    return [item["amount_cents"] for item in subscription["installments"]][:count]


def travel(day):
    clock.travel_to(datetime.combine(day, datetime.min.time()).replace(hour=10))


# --- partners -----------------------------------------------------------------------


def test_partner_registration_and_validation(ctx):
    acme = ctx.partner(city="Campinas", state="sp", phone="(19) 3000-0000")
    assert acme["name"] == "Acme Indústria"
    assert acme["state"] == "SP"
    assert (acme["agreements_count"], acme["employees"]) == (0, 0)
    assert ctx.client.get(f"/partners/{acme['id']}", headers=ctx.headers).json() == acme
    ctx.partner(name="Acme Indústria", cnpj="", expect=409)  # names are unique
    ctx.partner(name="Other", cnpj="12345678000190", expect=409)  # the same CNPJ, whatever the format
    ctx.partner(name="Bad CNPJ", cnpj="123", expect=422)
    ctx.partner(name="Bad state", cnpj="", state="XX", expect=422)
    ctx.partner(name="Bad email", cnpj="", email="not-an-email", expect=422)
    updated = ctx.client.put(
        f"/partners/{acme['id']}", headers=ctx.headers, json={"name": "Acme Indústria S.A.", "cnpj": acme["cnpj"], "active": False}
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["name"], updated.json()["active"]) == ("Acme Indústria S.A.", False)


def test_partner_with_agreements_or_logins_cannot_be_deleted(ctx):
    acme = ctx.partner()
    ctx.agreement(acme["id"])
    assert ctx.client.delete(f"/partners/{acme['id']}", headers=ctx.headers).status_code == 409
    lonely = ctx.partner(name="Lonely", cnpj="")
    login = ctx.client.post(
        "/users",
        headers=ctx.headers,
        json={"name": "HR", "email": "hr@lonely.example", "role": "partner", "password": "saude123", "partner_id": lonely["id"]},
    )
    assert login.status_code == 201, login.text
    assert ctx.client.delete(f"/partners/{lonely['id']}", headers=ctx.headers).status_code == 409
    ctx.client.delete(f"/users/{login.json()['id']}", headers=ctx.headers)
    assert ctx.client.delete(f"/partners/{lonely['id']}", headers=ctx.headers).status_code == 204
    assert ctx.client.get(f"/partners/{lonely['id']}", headers=ctx.headers).status_code == 404


def test_who_reads_and_who_writes_partners_and_agreements(ctx):
    acme = ctx.partner()
    terms = ctx.agreement(acme["id"])
    attendant = auth_as(ctx.client, "attendant")
    supervisor = auth_as(ctx.client, "supervisor")
    # The staff reads (the reception enrolls employees); admins and supervisors write.
    assert ctx.client.get("/partners", headers=attendant).status_code == 200
    assert ctx.client.get(f"/agreements/{terms['id']}", headers=attendant).status_code == 200
    ctx.partner(name="By attendant", cnpj="", headers=attendant, expect=403)
    ctx.agreement(acme["id"], headers=attendant, expect=403)
    ctx.update_agreement(terms, headers=attendant, expect=403, min_employees=5)
    assert ctx.client.post(f"/agreements/{terms['id']}/renew", headers=attendant).status_code == 403
    ctx.partner(name="By supervisor", cnpj="", headers=supervisor)
    ctx.update_agreement(terms, headers=supervisor, min_employees=3)
    # Clients, sellers and professionals have no part in it.
    employee = ctx.enroll(terms["id"])
    region = ctx.client.post(
        "/regions", headers=ctx.headers, json={"name": "SP", "areas": [{"state": "SP"}], "plans": [{"modality_id": ctx.plan["id"]}]}
    ).json()
    seller = ctx.client.post("/sellers", headers=ctx.headers, json={"name": "Ana", "region_id": region["id"]}).json()
    professional = ctx.client.post(
        "/professionals", headers=ctx.headers, json={"name": "Lorem", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()
    for headers in (
        auth_as(ctx.client, "client", customer_id=employee["customer_id"]),
        auth_as(ctx.client, "seller", seller_id=seller["id"]),
        auth_as(ctx.client, "professional", professional_id=professional["id"]),
    ):
        assert ctx.client.get("/partners", headers=headers).status_code == 403
        assert ctx.client.get("/agreements", headers=headers).status_code == 403
        assert ctx.client.get(f"/agreements/{terms['id']}/invoices", headers=headers).status_code == 403
        assert ctx.client.get("/portal", headers=headers).status_code == 403


# --- agreements ---------------------------------------------------------------------


def test_agreement_is_a_six_month_contract_with_negotiated_conditions(ctx):
    acme = ctx.partner()
    terms = ctx.agreement(
        acme["id"],
        plans=[
            {"modality_id": ctx.plan["id"], "discount_percent": 20, "grace_days": 0},
            {"modality_id": ctx.other_plan["id"], "discount_percent": 15},
        ],
        notes="Signed by the HR director.",
        ends_on="2030-01-01",  # ignored: six months
    )
    assert (terms["starts_on"], terms["ends_on"]) == ("2026-10-01", "2027-03-31")
    assert terms["partner_name"] == "Acme Indústria"
    assert (terms["min_employees"], terms["payer"], terms["status"], terms["terms_frozen"]) == (2, "company", "in_force", False)
    assert terms["company_share_percent"] == 100  # the company pays every fee
    assert terms["plans"] == [
        {
            "modality_id": ctx.other_plan["id"],
            "discount_percent": 15,
            "grace_days": None,
            "company_share_percent": None,
            "modality_name": "Plan Bar",
            "plan_value_cents": 19900,
            "value_cents": 16915,
            "plan_grace_days": 0,
        },
        {
            "modality_id": ctx.plan["id"],
            "discount_percent": 20,
            "grace_days": 0,
            "company_share_percent": None,
            "modality_name": "Plan Foo",
            "plan_value_cents": 9900,
            "value_cents": 7920,
            "plan_grace_days": 0,
        },
    ]
    assert (terms["reference_period"], terms["employees"], terms["conditions_met"], terms["missing_employees"]) == ("2026-10", 0, False, 2)
    listed = ctx.client.get(f"/agreements?partner_id={acme['id']}", headers=ctx.headers).json()
    assert [item["id"] for item in listed] == [terms["id"]]
    assert ctx.client.get("/agreements?status=ended", headers=ctx.headers).json() == []
    assert ctx.client.get("/agreements?status=bogus", headers=ctx.headers).status_code == 400
    partner = ctx.client.get(f"/partners/{acme['id']}", headers=ctx.headers).json()
    assert (partner["agreements_count"], partner["agreements_in_force"]) == (1, 1)


def test_agreement_validation(ctx):
    acme = ctx.partner()
    ctx.agreement(acme["id"], min_employees=0, expect=422)
    ctx.agreement(acme["id"], payer="bank", expect=422)
    ctx.agreement(acme["id"], plans=[], expect=422)  # an agreement covers at least one plan
    ctx.agreement(acme["id"], plans=[{"modality_id": ctx.plan["id"]}, {"modality_id": ctx.plan["id"]}], expect=422)
    ctx.agreement(acme["id"], plans=[{"modality_id": ctx.plan["id"], "discount_percent": 101}], expect=422)
    ctx.agreement(acme["id"], plans=[{"modality_id": 999}], expect=404)
    ctx.agreement(999, expect=404)


def test_agreement_status_follows_dates_and_activity(ctx):
    acme = ctx.partner()
    scheduled = ctx.agreement(acme["id"], name="Next", starts_on="2027-01-01")
    ended = ctx.agreement(acme["id"], name="Last", starts_on="2026-03-01")
    current = ctx.agreement(acme["id"])
    assert (scheduled["status"], scheduled["reference_period"]) == ("scheduled", "2027-01")
    assert (ended["status"], ended["ends_on"]) == ("ended", "2026-08-31")
    assert current["status"] == "in_force"
    assert ctx.update_agreement(current, active=False)["status"] == "suspended"
    ctx.client.put(f"/partners/{acme['id']}", headers=ctx.headers, json={"name": acme["name"], "cnpj": acme["cnpj"], "active": False})
    assert ctx.client.get(f"/agreements/{scheduled['id']}", headers=ctx.headers).json()["status"] == "suspended"


def test_terms_are_frozen_once_employees_are_enrolled(ctx):
    terms = ctx.agreement()
    # Before any enrollment the contract may still change (a new start moves its six months).
    terms = ctx.update_agreement(terms, starts_on="2026-11-01", min_employees=3)
    assert (terms["ends_on"], terms["min_employees"]) == ("2027-04-30", 3)
    terms = ctx.update_agreement(terms, starts_on=STARTS.isoformat(), min_employees=2)
    ctx.enroll(terms["id"])
    terms = ctx.client.get(f"/agreements/{terms['id']}", headers=ctx.headers).json()
    assert terms["terms_frozen"] is True
    for change in (
        {"min_employees": 5},
        {"payer": "employee"},
        {"starts_on": "2026-11-01"},
        {"plans": [{"modality_id": ctx.plan["id"], "discount_percent": 30, "grace_days": 0}]},
        {"partner_id": ctx.partner(name="Other", cnpj="")["id"]},
    ):
        response = ctx.update_agreement(terms, expect=409, **change)
        assert "frozen" in response["detail"]
    # Only the name, the notes and the activity change.
    renamed = ctx.update_agreement(terms, name="Acme first half", notes="Renewal in March.", active=False)
    assert (renamed["name"], renamed["notes"], renamed["status"]) == ("Acme first half", "Renewal in March.", "suspended")
    # An agreement with enrollments is suspended, not deleted.
    assert ctx.client.delete(f"/agreements/{terms['id']}", headers=ctx.headers).status_code == 409
    empty = ctx.agreement(terms["partner_id"], name="Empty")
    assert ctx.client.delete(f"/agreements/{empty['id']}", headers=ctx.headers).status_code == 204


# --- enrollments ----------------------------------------------------------------------


def test_enrollment_rules(ctx):
    acme = ctx.partner()
    terms = ctx.agreement(acme["id"])
    ctx.enroll(terms["id"], plan=ctx.other_plan, expect=409)  # a plan the agreement does not cover
    ctx.enroll(terms["id"], joined=date(2026, 9, 1), expect=409)  # before the contract starts
    ctx.enroll(terms["id"], expires=date(2027, 9, 30), expect=409)  # past its six months
    ctx.enroll(999, expect=404)
    ended = ctx.agreement(acme["id"], name="Old", starts_on="2026-03-01")
    ctx.enroll(ended["id"], joined=date(2026, 3, 1), expires=date(2026, 8, 31), expect=409)
    suspended = ctx.update_agreement(ctx.agreement(acme["id"], name="Suspended"), active=False)
    ctx.enroll(suspended["id"], expect=409)
    other = ctx.partner(name="Inactive Co", cnpj="", active=False)
    ctx.enroll(ctx.agreement(other["id"])["id"], expect=409)
    # A corporate enrollment is never a seller's sale.
    ctx.enroll(terms["id"], seller_id=1, expect=422)
    enrolled = ctx.enroll(terms["id"])
    assert (enrolled["agreement_id"], enrolled["agreement_name"], enrolled["partner_name"]) == (terms["id"], "Acme 2026/2", "Acme Indústria")
    assert enrolled["paid_by_company"] is True
    assert (enrolled["seller_id"], enrolled["region_id"], enrolled["discount_percent"]) == (None, None, 20)


def test_only_the_staff_enrolls_employees_here(ctx):
    terms = ctx.agreement()
    holder = ctx.customer("Self Service", city="Campinas", state="SP")
    client_headers = auth_as(ctx.client, "client", customer_id=holder)
    ctx.enroll(terms["id"], customer_id=holder, headers=client_headers, expect=403)
    quote = f"/subscriptions/quote?customer_id={holder}&modality_id={ctx.plan['id']}&agreement_id={terms['id']}"
    assert ctx.client.get(quote, headers=client_headers).status_code == 403
    region = ctx.client.post(
        "/regions", headers=ctx.headers, json={"name": "SP", "areas": [{"state": "SP"}], "plans": [{"modality_id": ctx.plan["id"]}]}
    ).json()
    seller = ctx.client.post("/sellers", headers=ctx.headers, json={"name": "Ana", "region_id": region["id"]}).json()
    seller_headers = auth_as(ctx.client, "seller", seller_id=seller["id"])
    ctx.enroll(terms["id"], customer_id=holder, headers=seller_headers, expect=403)
    assert ctx.client.get(quote, headers=seller_headers).status_code == 403
    ctx.enroll(terms["id"], customer_id=holder, headers=auth_as(ctx.client, "attendant"))


def test_employees_always_get_the_negotiated_conditions(ctx):
    terms = ctx.agreement(min_employees=3)
    first = ctx.enroll(terms["id"])
    # Alone, below the minimum: still the negotiated price (the company answers for the minimum).
    assert amounts(first) == [7920] * 6
    assert all(item["discount_cents"] == 1980 for item in first["installments"])
    assert first["monthly_amount_cents"] == 7920
    assert (first["agreement_employees"], first["agreement_min_employees"], first["agreement_conditions_met"]) == (1, 3, False)
    ctx.enroll(terms["id"])
    ctx.enroll(terms["id"])
    first = ctx.get(first["id"])
    assert amounts(first) == [7920] * 6  # nothing re-priced either way
    assert (first["agreement_employees"], first["agreement_conditions_met"]) == (3, True)
    agreement = ctx.client.get(f"/agreements/{terms['id']}", headers=ctx.headers).json()
    assert (agreement["employees"], agreement["conditions_met"], agreement["missing_employees"]) == (3, True, 0)


def test_enrollments_keep_the_conditions_they_were_sold_with(ctx):
    """Frozen terms: the discount is snapshotted on the enrollment."""
    terms = ctx.agreement(min_employees=1)
    enrolled = ctx.enroll(terms["id"])
    body = {k: enrolled[k] for k in ("customer_id", "modality_id", "joined_on", "expires_on")}
    edited = ctx.client.put(f"/subscriptions/{enrolled['id']}", headers=ctx.headers, json={**body, "expires_on": "2027-01-31"})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["discount_percent"], amounts(edited.json())) == (20, [7920] * 4)


def test_waiting_period_of_the_agreement(ctx):
    slow_plan = ensure_plan(ctx.client, ctx.headers, name="Plan Slow", grace_days=30)
    professional = ctx.client.post(
        "/professionals", headers=ctx.headers, json={"name": "Lorem", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()
    waived = ctx.agreement(min_employees=5, plans=[{"modality_id": slow_plan["id"], "discount_percent": 10, "grace_days": 0}])
    own = ctx.agreement(waived["partner_id"], name="Plan's own", plans=[{"modality_id": slow_plan["id"], "discount_percent": 10}])
    first = ctx.enroll(waived["id"], plan=slow_plan)
    assert first["usable_from"] == "2026-10-01"  # waived by the agreement, whatever the headcount
    assert ctx.enroll(own["id"], plan=slow_plan)["usable_from"] == "2026-10-31"  # null: the plan's 30 days
    ctx.pay(waived["id"], "2026-10", date(2026, 10, 20))
    booked = ctx.client.post(
        "/appointments",
        headers=ctx.headers,
        json={"customer_id": first["customer_id"], "professional_id": professional["id"], "marked_at": "2026-10-26T08:00"},
    )
    assert booked.status_code == 201, booked.text


def test_enrollment_quote(ctx):
    terms = ctx.agreement(min_employees=3)
    ctx.enroll(terms["id"])
    holder = ctx.customer()
    base = f"/subscriptions/quote?customer_id={holder}&modality_id={ctx.plan['id']}&agreement_id={terms['id']}"
    quote = ctx.client.get(base + "&joined_on=2026-10-22", headers=ctx.headers).json()
    assert quote["allowed"] is True
    assert (quote["employees"], quote["min_employees"], quote["conditions_met"]) == (2, 3, False)
    assert (quote["discount_percent"], quote["negotiated_discount_percent"], quote["monthly_amount_cents"]) == (20, 20, 7920)
    assert (quote["partner_name"], quote["payer"], quote["grace_days"], quote["agreement_ends_on"]) == (
        "Acme Indústria",
        "company",
        0,
        "2027-03-31",
    )
    outside = ctx.client.get(base + "&joined_on=2027-01-01&expires_on=2027-12-31", headers=ctx.headers).json()
    assert outside["allowed"] is False and "must fit" in outside["reason"]
    not_covered = ctx.client.get(
        f"/subscriptions/quote?customer_id={holder}&modality_id={ctx.other_plan['id']}&agreement_id={terms['id']}", headers=ctx.headers
    ).json()
    assert not_covered["allowed"] is False and "not covered" in not_covered["reason"]
    assert ctx.client.get(base + "&seller_id=1", headers=ctx.headers).status_code == 400


def test_dependents_are_priced_with_the_negotiated_discount(ctx):
    terms = ctx.agreement(min_employees=1)
    enrolled = ctx.enroll(terms["id"])
    for name in ("Kid 1", "Kid 2", "Kid 3"):  # the third is above the plan's limit
        response = ctx.client.post(
            f"/customers/{enrolled['customer_id']}/dependents", headers=ctx.headers, json={"name": name, "birth_date": "2015-01-01"}
        )
        assert response.status_code == 201, response.text
    refreshed = ctx.get(enrolled["id"])
    assert refreshed["list_amount_cents"] == 11400
    assert amounts(refreshed, 2) == [9120, 9120]  # (99.00 + 15.00) - 20%


def test_moving_in_and_out_of_an_agreement(ctx):
    terms = ctx.agreement(min_employees=1)
    direct = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": ctx.customer(), "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-03-31"},
    ).json()
    body = {k: direct[k] for k in ("customer_id", "modality_id", "joined_on", "expires_on")}
    moved = ctx.client.put(f"/subscriptions/{direct['id']}", headers=ctx.headers, json={**body, "agreement_id": terms["id"]})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["agreement_id"], amounts(moved.json(), 2)) == (terms["id"], [7920, 7920])
    kept = ctx.client.put(f"/subscriptions/{direct['id']}", headers=ctx.headers, json=body)  # omitted: unchanged
    assert kept.json()["agreement_id"] == terms["id"]
    out = ctx.client.put(f"/subscriptions/{direct['id']}", headers=ctx.headers, json={**body, "agreement_id": None})
    assert (out.json()["agreement_id"], out.json()["discount_percent"], amounts(out.json(), 2)) == (None, 0, [9900, 9900])
    assert ctx.client.get(f"/agreements/{terms['id']}", headers=ctx.headers).json()["employees"] == 0


def test_the_employee_sees_its_corporate_plan_but_the_company_manages_it(ctx):
    terms = ctx.agreement(min_employees=1)
    enrolled = ctx.enroll(terms["id"])
    employee = auth_as(ctx.client, "client", customer_id=enrolled["customer_id"])
    seen = ctx.get(enrolled["id"], headers=employee)
    assert (seen["partner_name"], seen["paid_by_company"], seen["discount_percent"]) == ("Acme Indústria", True, 20)
    # The company's headcount is internal.
    assert (seen["agreement_employees"], seen["agreement_min_employees"], seen["agreement_conditions_met"]) == (None, None, None)
    body = {k: enrolled[k] for k in ("customer_id", "modality_id", "joined_on", "expires_on")}
    assert ctx.client.put(f"/subscriptions/{enrolled['id']}", headers=employee, json={**body, "expires_on": "2027-01-31"}).status_code == 403
    assert ctx.client.post(f"/subscriptions/{enrolled['id']}/cancel", headers=employee).status_code == 403
    upgrade = {**body, "modality_id": ctx.other_plan["id"], "joined_on": "2026-12-01", "expires_on": "2027-11-30", "upgrade": True}
    assert ctx.client.post("/subscriptions", headers=employee, json=upgrade).status_code == 403
    # The staff may move the employee to a personal plan (e.g. it left the company).
    moved = ctx.client.post("/subscriptions", headers=ctx.headers, json=upgrade)
    assert moved.status_code == 201, moved.text
    assert moved.json()["agreement_id"] is None
    assert ctx.get(enrolled["id"])["expires_on"] == "2026-11-30"


def test_upgrade_inside_the_agreement_counts_the_employee_once(ctx):
    terms = ctx.agreement(
        min_employees=2,
        plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}, {"modality_id": ctx.other_plan["id"], "discount_percent": 10}],
    )
    ctx.enroll(terms["id"])
    second = ctx.enroll(terms["id"])
    upgraded = ctx.enroll(terms["id"], customer_id=second["customer_id"], plan=ctx.other_plan, joined=date(2026, 12, 15), upgrade=True)
    assert amounts(upgraded, 2) == [17910, 17910]
    assert ctx.get(second["id"])["expires_on"] == "2026-12-14"
    december = ctx.invoice(terms["id"], "2026-12")
    assert (december["employees"], december["installments"], december["below_minimum"]) == (2, 3, False)


def test_listing_enrollments_by_agreement_and_partner(ctx):
    acme = ctx.partner()
    terms = ctx.agreement(acme["id"])
    other = ctx.agreement(acme["id"], name="Acme Branch")
    one = ctx.enroll(terms["id"])
    two = ctx.enroll(other["id"])
    ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": ctx.customer(), "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-03-31"},
    )
    by_agreement = ctx.client.get(f"/subscriptions?agreement_id={terms['id']}", headers=ctx.headers).json()
    assert [row["id"] for row in by_agreement] == [one["id"]]
    by_partner = ctx.client.get(f"/subscriptions?partner_id={acme['id']}", headers=ctx.headers).json()
    assert sorted(row["id"] for row in by_partner) == sorted([one["id"], two["id"]])
    assert ctx.client.get("/partners", headers=ctx.headers).json()[0]["employees"] == 2


# --- the minimum and the shortfall -------------------------------------------------------------


def test_below_the_minimum_the_company_pays_the_discounts_of_the_month(ctx):
    terms = ctx.agreement(min_employees=3)
    ctx.enroll(terms["id"])
    ctx.enroll(terms["id"], joined=date(2026, 10, 15))
    october = ctx.invoice(terms["id"], "2026-10")
    assert (october["employees"], october["min_employees"], october["below_minimum"]) == (2, 3, True)
    assert (october["amount_cents"], october["discount_cents"], october["list_cents"]) == (15840, 3960, 19800)
    # Projected while the month runs: the headcount may still change.
    assert (october["shortfall_cents"], october["shortfall_final"], october["shortfall_due_on"]) == (3960, False, "2026-11-10")
    assert (october["company_cents"], october["company_open_cents"]) == (15840 + 3960, 15840 + 3960)
    # The company pays the fees now; the shortfall waits for the end of the month.
    paid = ctx.pay(terms["id"], "2026-10", date(2026, 10, 20))
    assert (paid["company_fees_paid_cents"], paid["company_fees_open_cents"], paid["shortfall_paid_on"], paid["status"]) == (15840, 0, None, "open")
    assert ctx.pay(terms["id"], "2026-10", date(2026, 10, 21), expect=409)["detail"].startswith("The shortfall of 2026-10 is charged once the month ends")
    # A third employee in November meets the minimum: no shortfall for it.
    ctx.enroll(terms["id"], joined=date(2026, 11, 1))
    november = ctx.invoice(terms["id"], "2026-11")
    assert (november["employees"], november["below_minimum"], november["shortfall_cents"]) == (3, False, 0)
    travel(date(2026, 11, 3))
    october = ctx.invoice(terms["id"], "2026-10")
    assert (october["shortfall_final"], october["status"], october["company_open_cents"]) == (True, "open", 3960)
    paid = ctx.pay(terms["id"], "2026-10", date(2026, 11, 3))
    assert (paid["shortfall_cents"], paid["shortfall_paid_on"], paid["status"], paid["company_open_cents"]) == (3960, "2026-11-03", "paid", 0)
    assert ctx.pay(terms["id"], "2026-10", date(2026, 11, 3), expect=409)["detail"] == "The invoice of 2026-10 is already paid"


def test_an_unpaid_shortfall_becomes_overdue_and_can_be_undone(ctx):
    terms = ctx.agreement(min_employees=5)
    ctx.enroll(terms["id"])
    ctx.pay(terms["id"], "2026-10", date(2026, 10, 20))
    travel(date(2026, 11, 11))
    assert ctx.invoice(terms["id"], "2026-10")["status"] == "overdue"
    agreement = ctx.client.get(f"/agreements/{terms['id']}", headers=ctx.headers).json()
    assert agreement["shortfall_open_cents"] == 1980
    ctx.pay(terms["id"], "2026-10", date(2026, 11, 11))
    undone = ctx.client.delete(f"/agreements/{terms['id']}/invoices/2026-10/shortfall", headers=ctx.headers)
    assert undone.status_code == 200, undone.text
    assert (undone.json()["shortfall_paid_on"], undone.json()["status"]) == (None, "overdue")
    assert ctx.client.delete(f"/agreements/{terms['id']}/invoices/2026-10/shortfall", headers=ctx.headers).status_code == 404


def test_the_paid_shortfall_is_frozen(ctx):
    terms = ctx.agreement(min_employees=3)
    ctx.enroll(terms["id"])
    travel(date(2026, 11, 2))
    ctx.pay(terms["id"], "2026-10", date(2026, 11, 2))
    # A late enrollment backdated into October does not rewrite what was paid.
    ctx.enroll(terms["id"], joined=date(2026, 10, 20))
    october = ctx.invoice(terms["id"], "2026-10")
    assert (october["employees"], october["shortfall_cents"], october["shortfall_paid_on"]) == (2, 1980, "2026-11-02")


def test_when_employees_pay_the_company_pays_only_the_shortfall(ctx):
    terms = ctx.agreement(min_employees=2, payer="employee")
    enrolled = ctx.enroll(terms["id"])
    october = ctx.invoice(terms["id"], "2026-10")
    assert (october["company_cents"], october["shortfall_cents"]) == (1980, 1980)
    assert ctx.pay(terms["id"], "2026-10", date(2026, 10, 20), expect=409)["detail"].startswith("The shortfall of 2026-10")
    ctx.enroll(terms["id"])  # the minimum is met: nothing for the company
    october = ctx.invoice(terms["id"], "2026-10")
    assert (october["company_cents"], october["status"]) == (0, "nothing_due")
    assert ctx.pay(terms["id"], "2026-10", date(2026, 10, 20), expect=409)["detail"].startswith("Nothing for the company to pay")
    # The employee pays its own fee at the negotiated price, on its subscription.
    item = enrolled["installments"][0]
    paid = ctx.client.put(f"/subscriptions/{enrolled['id']}/installments/{item['id']}", headers=ctx.headers, json={"paid_on": "2026-10-05"})
    assert paid.json()["installments"][0]["paid_cents"] == 7920


def test_shortfall_is_taxed_and_reported_as_plan_revenue(ctx):
    response = ctx.client.post(
        "/tax-rules",
        headers=ctx.headers,
        json={"code": "ISS", "name": "ISS", "level": "municipal", "rate_bp": 500, "applies_to": "subscription", "effective_from": "2026-01-01"},
    )
    assert response.status_code == 201, response.text
    terms = ctx.agreement(min_employees=2)
    enrolled = ctx.enroll(terms["id"])
    travel(date(2026, 11, 2))
    ctx.pay(terms["id"], "2026-10", date(2026, 11, 2))
    assert ctx.get(enrolled["id"])["installments"][0]["tax_cents"] == 396  # 5% of 79.20
    finance = ctx.client.get("/reports/finance?granularity=month&start=2026-11-01&end=2026-11-30", headers=ctx.headers).json()
    totals = finance["totals"]
    assert (totals["agreement_charges"], totals["agreement_charges_cents"]) == (1, 1980)
    assert totals["plan_revenue_cents"] == 7920 + 1980
    assert totals["tax_cents"] == 396 + 99  # 5% of the shortfall too
    taxes = ctx.client.get("/reports/taxes?granularity=month&start=2026-11-01&end=2026-11-30", headers=ctx.headers).json()
    assert taxes["totals"]["tax_cents"] == 396 + 99


def test_invoice_payment_rules(ctx):
    terms = ctx.agreement(min_employees=1)
    ctx.enroll(terms["id"])
    base = f"/agreements/{terms['id']}/invoices"
    assert ctx.client.get(f"{base}/october", headers=ctx.headers).status_code == 400
    assert ctx.client.get(f"{base}/2030-01", headers=ctx.headers).status_code == 404
    assert ctx.client.post(f"{base}/2030-01/pay", headers=ctx.headers, json={"paid_on": "2026-10-20"}).status_code == 404
    assert ctx.client.post(f"{base}/2026-10/pay", headers=ctx.headers, json={"paid_on": "2030-01-01"}).status_code == 422
    # A month is paid from its first day.
    assert ctx.pay(terms["id"], "2026-11", date(2026, 10, 20), expect=409)["detail"].startswith("The invoice of 2026-11 is paid from")
    # The reception registers the company's payment like any other.
    ctx.pay(terms["id"], "2026-10", date(2026, 10, 20), headers=auth_as(ctx.client, "attendant"))


def test_company_invoice_is_paid_at_once(ctx, outbox):
    terms = ctx.agreement(min_employees=2)
    first = ctx.enroll(terms["id"])
    second = ctx.enroll(terms["id"], joined=date(2026, 10, 15))
    invoices = ctx.client.get(f"/agreements/{terms['id']}/invoices", headers=ctx.headers).json()
    assert [item["period"] for item in invoices] == ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    detail = ctx.invoice(terms["id"], "2026-10")
    assert [line["subscription_id"] for line in detail["lines"]] == [first["id"], second["id"]]
    employee = auth_as(ctx.client, "client", customer_id=first["customer_id"])
    outbox.sent.clear()
    paid = ctx.pay(terms["id"], "2026-10", date(2026, 10, 20))
    assert (paid["status"], paid["company_fees_paid_cents"], paid["company_open_cents"], paid["company_paid_on"]) == ("paid", 15840, 0, "2026-10-20")
    assert all(line["status"] == "paid" for line in paid["lines"])
    for row in (first, second):
        assert ctx.get(row["id"])["status"] == "ativa"  # the first fee is paid: the plans are active
    ctx.client.post("/queue/run", headers=ctx.headers)
    subjects = {mail["subject"] for mail in outbox.sent if mail["to"] == "rh@acme.example"}
    assert "Invoice 2026-10 paid — Acme Indústria" in subjects
    assert "installment.paid" in [row["event"] for row in ctx.client.get("/notifications", headers=employee).json()]


# --- notices ----------------------------------------------------------------------------------


def notices(ctx):
    rows = ctx.client.get("/notifications", headers=ctx.headers).json()
    return [row["title"] for row in rows if row["event"] == "agreement.conditions"]


def test_reaching_and_losing_the_minimum_is_notified(ctx):
    terms = ctx.agreement(min_employees=2)
    ctx.enroll(terms["id"])
    ctx.outbox.sent.clear()
    second = ctx.enroll(terms["id"])
    assert notices(ctx) == ["Acme Indústria: minimum of 2 employees reached"]
    ctx.client.post("/queue/run", headers=ctx.headers)
    assert "rh@acme.example" in {mail["to"] for mail in ctx.outbox.sent}
    # Withdrawn altogether (deleted): October bills one employee again.
    ctx.client.delete(f"/subscriptions/{second['id']}", headers=ctx.headers)
    assert notices(ctx)[0] == "Acme Indústria: below the minimum of 2 employees"


def test_the_worker_tells_when_a_new_month_changes_the_minimum(ctx):
    terms = ctx.agreement(min_employees=2)
    ctx.enroll(terms["id"])
    second = ctx.enroll(terms["id"])
    # Leaves at the end of October: its November fee is no longer owed.
    ctx.client.post(f"/subscriptions/{second['id']}/cancel", headers=ctx.headers, json={"cancelled_on": "2026-10-31"})
    assert notices(ctx) == ["Acme Indústria: minimum of 2 employees reached"]  # October still bills two
    assert ctx.client.post("/queue/run", headers=ctx.headers).json()["agreements"] == {"changed": 0}
    travel(date(2026, 11, 1))
    assert ctx.client.post("/queue/run", headers=ctx.headers).json()["agreements"] == {"changed": 1}
    assert notices(ctx)[0] == "Acme Indústria: below the minimum of 2 employees"
    assert ctx.client.post("/queue/run", headers=ctx.headers).json()["agreements"] == {"changed": 0}  # told once


# --- renewals and the next plan ------------------------------------------------------------------


def test_renewal_carries_the_employees_into_the_next_six_months(ctx):
    terms = ctx.agreement(
        min_employees=2,
        plans=[{"modality_id": ctx.plan["id"], "discount_percent": 20}, {"modality_id": ctx.other_plan["id"], "discount_percent": 10}],
    )
    staying = ctx.enroll(terms["id"])
    leaving = ctx.enroll(terms["id"], expires=date(2027, 1, 31))  # leaves before the end: not carried
    other_plan = ctx.enroll(terms["id"], plan=ctx.other_plan)
    renewed = ctx.client.post(
        f"/agreements/{terms['id']}/renew",
        headers=ctx.headers,
        json={"name": "Acme 2027/1", "plans": [{"modality_id": ctx.plan["id"], "discount_percent": 25}]},  # renegotiated
    )
    assert renewed.status_code == 201, renewed.text
    renewed = renewed.json()
    assert (renewed["name"], renewed["starts_on"], renewed["ends_on"], renewed["min_employees"]) == ("Acme 2027/1", "2027-04-01", "2027-09-30", 2)
    assert (renewed["terms_frozen"], renewed["enrollments_count"]) == (True, 1)  # Plan Bar is no longer covered
    carried = ctx.client.get(f"/subscriptions?agreement_id={renewed['id']}", headers=ctx.headers).json()
    assert [(row["customer_id"], row["joined_on"], row["expires_on"], row["discount_percent"]) for row in carried] == [
        (staying["customer_id"], "2027-04-01", "2027-09-30", 25)
    ]
    # The current contract keeps its terms and its employees until its end.
    assert ctx.get(staying["id"])["expires_on"] == "2027-03-31"
    assert amounts(ctx.get(staying["id"])) == [7920] * 6
    assert leaving["id"] and other_plan["id"]


def test_renewal_without_the_employees_and_same_terms(ctx):
    terms = ctx.agreement(min_employees=4)
    ctx.enroll(terms["id"])
    renewed = ctx.client.post(f"/agreements/{terms['id']}/renew", headers=ctx.headers, json={"carry_employees": False}).json()
    assert (renewed["name"], renewed["min_employees"], renewed["enrollments_count"], renewed["terms_frozen"]) == (
        "Acme 2026/2 (renewed)",
        4,
        0,
        False,
    )
    assert [(p["modality_id"], p["discount_percent"], p["grace_days"]) for p in renewed["plans"]] == [(ctx.plan["id"], 20, 0)]


def test_a_new_plan_after_the_current_one_ended_is_not_an_upgrade(ctx):
    """One plan at a time means no overlap: the next plan of a customer whose
    plan ended (e.g. the next contract) is simply created, and an upgrade
    never stretches a plan that already ended."""
    holder = ctx.customer()
    old = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-01-01", "expires_on": "2026-06-30"},
    ).json()
    following = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-11-01", "expires_on": "2027-04-30"},
    )
    assert following.status_code == 201, following.text
    assert ctx.get(old["id"])["expires_on"] == "2026-06-30"  # untouched
    # ... but an overlapping one is still refused, and so is stretching a plan over the next one.
    overlapping = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2027-04-01", "expires_on": "2027-09-30"},
    )
    assert overlapping.status_code == 409
    stretched = ctx.client.put(
        f"/subscriptions/{old['id']}",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-01-01", "expires_on": "2026-12-31"},
    )
    assert stretched.status_code == 409


# --- reports and home -------------------------------------------------------------------------


def test_plans_report_and_dashboard_cover_the_agreements(ctx):
    terms = ctx.agreement(min_employees=2)
    ctx.agreement(terms["partner_id"], name="Far from it", min_employees=50)
    ctx.enroll(terms["id"])
    ctx.enroll(terms["id"])
    ctx.pay(terms["id"], "2026-10", date(2026, 10, 20))
    report = ctx.client.get("/reports/plans?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.headers).json()
    rows = {row["agreement_name"]: row for row in report["by_agreement"]}
    assert rows["Acme 2026/2"]["enrolled"] == 2
    assert (rows["Acme 2026/2"]["payments"], rows["Acme 2026/2"]["revenue_cents"], rows["Acme 2026/2"]["discount_cents"]) == (2, 15840, 3960)
    assert (rows["Acme 2026/2"]["employees_month"], rows["Acme 2026/2"]["conditions_met"]) == (2, True)
    assert (rows["Far from it"]["employees_month"], rows["Far from it"]["conditions_met"]) == (0, False)
    home = ctx.client.get("/dashboard", headers=auth_as(ctx.client, "attendant")).json()
    assert (home["agreements_in_force"], home["agreements_below_minimum"]) == (2, 1)

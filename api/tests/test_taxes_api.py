"""API tests: company settings, tax rules (admin only), tax snapshots on
appointments and plan payments, tax/finance reports and the dashboard."""

from datetime import date, datetime, timedelta

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock

MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)
TODAY = clock.today()


def rule(**overrides):
    base = {
        "code": "ISS",
        "name": "ISS",
        "level": "municipal",
        "rate_bp": 500,
        "applies_to": "all",
        "base": "amount",
        "effective_from": "2026-01-01",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def ctx(client):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.supervisor = auth_as(client, "supervisor")
    c.attendant = auth_as(client, "attendant")
    # Clinic 1 ("Clinic Foo", Sample) gets a location; a second clinic elsewhere.
    foo = client.get("/clinics/1", headers=admin).json()
    client.put("/clinics/1", headers=admin, json={**foo, "state": "SP", "ibge_code": "3550308", "cnpj": "00.000.000/0001-00"})
    c.clinic_bar = client.post(
        "/clinics", headers=admin, json={"name": "Clinic Bar", "city": "Example", "state": "RJ", "ibge_code": "3304557", "fee_cents": 2000}
    ).json()["id"]
    client.put("/company", headers=admin, json={"name": "Hollic Health Ltda", "cnpj": "11.111.111/0001-11", "city": "Sample", "state": "SP", "ibge_code": "3550308", "tax_regime": "Lucro Presumido"})
    c.plan = ensure_plan(client, admin, discount_percent=50)  # gross 15000 -> amount 7500
    c.pro_foo = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    c.pro_bar = client.post(
        "/professionals", headers=admin, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": c.clinic_bar, "price_cents": 5000}
    ).json()["id"]
    c.holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    c.client_headers = auth_as(client, "client", customer_id=c.holder)

    def add_rule(**overrides):
        response = client.post("/tax-rules", headers=admin, json=rule(**overrides))
        assert response.status_code == 201, response.text
        return response.json()

    def book(professional=None, marked_at=MONDAY_8, status="agendada"):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": c.holder, "professional_id": professional or c.pro_foo, "marked_at": marked_at.isoformat()},
        )
        assert response.status_code == 201, response.text
        if status == "realizada":  # completion is the professional's signature
            sign(client, response.json()["id"])
            return client.get(f"/appointments/{response.json()['id']}", headers=admin).json()
        return response.json()

    c.add_rule = add_rule
    c.book = book
    return c


def standard_rules(ctx):
    ctx.add_rule(code="PIS", name="PIS", level="federal", rate_bp=65)
    ctx.add_rule(code="COFINS", name="COFINS", level="federal", rate_bp=300)
    ctx.add_rule(code="ISS", name="ISS", level="municipal", rate_bp=500)  # default
    ctx.add_rule(code="ISS", name="ISS", level="municipal", rate_bp=200, state="RJ", city="Example")


# --- company and rules: access -----------------------------------------------------------


def test_company_settings_read_by_staff_written_by_admin(ctx):
    assert ctx.client.get("/company", headers=ctx.attendant).json()["name"] == "Hollic Health Ltda"
    assert ctx.client.get("/company", headers=ctx.client_headers).status_code == 403
    body = {"name": "X", "state": "sp", "ibge_code": "3550308"}
    assert ctx.client.put("/company", headers=ctx.supervisor, json=body).status_code == 403
    assert ctx.client.put("/company", headers=ctx.attendant, json=body).status_code == 403
    updated = ctx.client.put("/company", headers=ctx.admin, json=body)
    assert updated.status_code == 200
    assert updated.json()["state"] == "SP"
    assert ctx.client.put("/company", headers=ctx.admin, json={"name": "X", "ibge_code": "12"}).status_code == 422


@pytest.mark.parametrize("role, expected", [("admin", 201), ("supervisor", 403), ("attendant", 403), ("client", 403)])
def test_only_admins_create_tax_rules(ctx, role, expected):
    headers = ctx.client_headers if role == "client" else {"admin": ctx.admin, "supervisor": ctx.supervisor, "attendant": ctx.attendant}[role]
    assert ctx.client.post("/tax-rules", headers=headers, json=rule()).status_code == expected


def test_staff_read_rules_clients_do_not(ctx):
    created = ctx.add_rule()
    assert ctx.client.get("/tax-rules", headers=ctx.attendant).status_code == 200
    assert ctx.client.get(f"/tax-rules/{created['id']}", headers=ctx.supervisor).json()["rate_label"] == "5%"
    assert ctx.client.get("/tax-rules", headers=ctx.client_headers).status_code == 403
    assert ctx.client.put(f"/tax-rules/{created['id']}", headers=ctx.supervisor, json=rule(rate_bp=1)).status_code == 403
    assert ctx.client.delete(f"/tax-rules/{created['id']}", headers=ctx.attendant).status_code == 403


def test_rule_validation(ctx):
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(level="county")).status_code == 422
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(rate_bp=10001)).status_code == 422
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(applies_to="everything")).status_code == 422
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(base="net")).status_code == 422
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(effective_to="2025-01-01")).status_code == 422
    assert ctx.client.post("/tax-rules", headers=ctx.admin, json=rule(clinic_id=9999)).status_code == 404
    created = ctx.add_rule(code=" iss ", state="sp", city="  ")
    assert created["code"] == "ISS" and created["state"] == "SP" and created["city"] is None


def test_rule_crud_and_names(ctx):
    created = ctx.add_rule(clinic_id=ctx.clinic_bar, specialty_id=1, modality_id=ctx.plan["id"])
    assert (created["clinic_name"], created["specialty_name"], created["modality_name"]) == ("Clinic Bar", "Clínico geral", "Plan Foo")
    updated = ctx.client.put(f"/tax-rules/{created['id']}", headers=ctx.admin, json=rule(rate_bp=250, notes="changed"))
    assert updated.status_code == 200
    assert updated.json()["rate_label"] == "2.5%"
    assert updated.json()["clinic_id"] is None
    listed = ctx.client.get("/tax-rules?code=iss", headers=ctx.admin).json()
    assert [row["id"] for row in listed] == [created["id"]]
    assert ctx.client.delete(f"/tax-rules/{created['id']}", headers=ctx.admin).status_code == 204
    assert ctx.client.get(f"/tax-rules/{created['id']}", headers=ctx.admin).status_code == 404


def test_rule_used_by_history_is_deactivated_not_deleted(ctx):
    iss = ctx.add_rule()
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book()
    assert ctx.client.delete(f"/tax-rules/{iss['id']}", headers=ctx.admin).status_code == 204
    kept = ctx.client.get(f"/tax-rules/{iss['id']}", headers=ctx.admin)
    assert kept.status_code == 200
    assert kept.json()["active"] is False
    assert ctx.client.get("/tax-rules?include_inactive=false", headers=ctx.admin).json() == []


# --- preview ---------------------------------------------------------------------------


def test_preview_resolves_by_clinic_location(ctx):
    standard_rules(ctx)
    foo = ctx.client.get("/tax-rules/preview?kind=appointment&amount_cents=10000&clinic_id=1&specialty_id=1", headers=ctx.attendant).json()
    assert {line["code"]: line["amount_cents"] for line in foo["lines"]} == {"PIS": 65, "COFINS": 300, "ISS": 500}
    assert foo["tax_cents"] == 865 and foo["net_cents"] == 9135
    assert foo["lines"][-1]["ibge_code"] == "3550308"
    bar = ctx.client.get(f"/tax-rules/preview?kind=appointment&amount_cents=10000&clinic_id={ctx.clinic_bar}", headers=ctx.attendant).json()
    assert {line["code"]: line["amount_cents"] for line in bar["lines"]}["ISS"] == 200
    plan_fee = ctx.client.get("/tax-rules/preview?kind=subscription&amount_cents=9900", headers=ctx.attendant).json()
    assert {line["code"]: line["amount_cents"] for line in plan_fee["lines"]}["ISS"] == 495  # company in Sample/SP
    assert ctx.client.get("/tax-rules/preview?kind=other", headers=ctx.attendant).status_code == 400
    assert ctx.client.get("/tax-rules/preview?clinic_id=9999", headers=ctx.attendant).status_code == 404


# --- snapshots on appointments ------------------------------------------------------------


def test_appointment_stores_tax_lines_and_net(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    body = ctx.book()
    assert body["amount_cents"] == 7500
    assert {line["code"]: line["amount_cents"] for line in body["taxes"]} == {"PIS": 49, "COFINS": 225, "ISS": 375}
    assert body["tax_cents"] == 649
    assert body["net_cents"] == 7500 - 649
    iss = next(line for line in body["taxes"] if line["code"] == "ISS")
    assert (iss["state"], iss["city"], iss["ibge_code"], iss["rate_bp"], iss["base_cents"]) == ("SP", "Sample", "3550308", 500, 7500)


def test_appointment_taxes_follow_the_clinic_location(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    body = ctx.book(professional=ctx.pro_bar)
    iss = next(line for line in body["taxes"] if line["code"] == "ISS")
    assert iss["rate_bp"] == 200 and iss["city"] == "Example" and iss["state"] == "RJ"
    assert body["tax_cents"] == 49 + 225 + 150


def test_specialty_and_plan_specific_rules(ctx):
    ctx.add_rule(code="ISS", rate_bp=500)
    ctx.add_rule(code="ISS", rate_bp=300, specialty_id=1)
    ctx.add_rule(code="ISS", rate_bp=100, specialty_id=1, modality_id=ctx.plan["id"])
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    body = ctx.book()
    assert body["taxes"][0]["rate_bp"] == 100  # specialty + plan is the most specific


def test_quote_shows_taxes_included(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    quote = ctx.client.get(f"/appointments/quote?customer_id={ctx.holder}&professional_id={ctx.pro_foo}&on=2026-10-26", headers=ctx.admin).json()
    assert quote["amount_cents"] == 7500
    assert quote["tax_cents"] == 649
    assert len(quote["taxes"]) == 3


def test_tax_snapshot_survives_rule_changes_but_repricing_recomputes(ctx):
    iss = ctx.add_rule(code="ISS", rate_bp=500)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    item = ctx.book()
    assert item["tax_cents"] == 375
    ctx.client.put(f"/tax-rules/{iss['id']}", headers=ctx.admin, json=rule(rate_bp=200))
    base = {"customer_id": ctx.holder, "professional_id": ctx.pro_foo, "marked_at": MONDAY_8.isoformat()}
    notes = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "notes": "x"}).json()
    assert notes["tax_cents"] == 375  # untouched
    moved = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "professional_id": ctx.pro_bar}).json()
    assert moved["tax_cents"] == 150  # repriced with the current rule (2 %)


def test_rules_apply_by_service_date(ctx):
    ctx.add_rule(code="ISS", rate_bp=500, effective_from="2026-01-01", effective_to="2026-10-31")
    ctx.add_rule(code="ISS", rate_bp=400, effective_from="2026-11-01")
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    october = ctx.book(marked_at=MONDAY_8)
    november = ctx.book(marked_at=datetime(2026, 11, 2, 8, 0))
    assert october["taxes"][0]["rate_bp"] == 500
    assert november["taxes"][0]["rate_bp"] == 400
    # Moving the October appointment into November (same price) re-applies the rules.
    moved = ctx.client.put(
        f"/appointments/{october['id']}",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.pro_foo, "marked_at": datetime(2026, 11, 3, 8, 0).isoformat()},
    ).json()
    assert moved["taxes"][0]["rate_bp"] == 400


def test_appointment_without_rules_has_no_tax(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    body = ctx.book()
    assert body["tax_cents"] == 0 and body["net_cents"] == body["amount_cents"] and body["taxes"] == []


# --- snapshots on plan payments -----------------------------------------------------------


def test_installment_payment_is_taxed_at_the_company_location(ctx):
    standard_rules(ctx)
    sub = ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    ).json()
    first = sub["installments"][0]
    assert first["tax_cents"] == 0
    paid = ctx.client.put(f"/subscriptions/{sub['id']}/installments/{first['id']}", headers=ctx.admin, json={"paid_on": "2026-10-01"}).json()
    paid_first = paid["installments"][0]
    assert paid_first["tax_cents"] == 64 + 297 + 495  # 0.65 % + 3 % + 5 % of 9900
    undone = ctx.client.put(f"/subscriptions/{sub['id']}/installments/{first['id']}", headers=ctx.admin, json={"paid_on": None}).json()
    assert undone["installments"][0]["tax_cents"] == 0


def test_subscription_only_rules_do_not_hit_appointments(ctx):
    ctx.add_rule(code="ISS", rate_bp=500, applies_to="subscription")
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    assert ctx.book()["tax_cents"] == 0


# --- reports ------------------------------------------------------------------------------


def test_taxes_report(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)  # payments today at Sample/SP
    done = ctx.book(professional=ctx.pro_foo, marked_at=MONDAY_8, status="realizada")  # taxes 649
    ctx.book(professional=ctx.pro_bar, marked_at=MONDAY_9, status="realizada")  # taxes 424
    ctx.book(professional=ctx.pro_foo, marked_at=datetime(2026, 10, 27, 8, 0))  # scheduled: excluded by default
    report = ctx.client.get("/reports/taxes?granularity=week&start=2026-10-26&end=2026-11-01", headers=ctx.admin).json()
    assert report["basis"] == "completed"
    assert report["totals"]["services"] == 2
    assert report["totals"]["amount_cents"] == 15000
    assert report["totals"]["tax_cents"] == 649 + 424
    assert report["totals"]["net_cents"] == 15000 - 1073
    by_code = {row["key"]: row for row in report["by_code"]}
    assert by_code["ISS"]["tax_cents"] == 375 + 150
    assert by_code["PIS"]["services"] == 2
    by_level = {row["key"]: row["tax_cents"] for row in report["by_level"]}
    assert by_level == {"federal": 2 * (49 + 225), "municipal": 525}
    jurisdictions = {row["label"]: row["tax_cents"] for row in report["by_jurisdiction"]}
    assert jurisdictions["Sample / SP (IBGE 3550308)"] == 375
    assert jurisdictions["Example / RJ (IBGE 3304557)"] == 150
    assert jurisdictions["Federal"] == 548
    assert {row["key"] for row in report["by_source"]} == {"appointment"}
    assert report["periods"][0]["period"] == "2026-W44"
    assert report["periods"][0]["by_code"]
    everything = ctx.client.get("/reports/taxes?start=2026-10-26&end=2026-11-01&basis=all", headers=ctx.admin).json()
    assert everything["totals"]["services"] == 3
    assert ctx.client.get("/reports/taxes?basis=maybe", headers=ctx.admin).status_code == 400
    by_clinic = ctx.client.get(f"/reports/taxes?start=2026-10-26&end=2026-11-01&clinic_id={ctx.clinic_bar}", headers=ctx.admin).json()
    assert by_clinic["totals"]["tax_cents"] == 424
    assert done["tax_cents"] == 649


def test_taxes_report_includes_plan_fees_and_ledger_csv(ctx):
    standard_rules(ctx)
    sub = ctx.client.post(
        "/subscriptions",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    ).json()
    ctx.client.put(f"/subscriptions/{sub['id']}/installments/{sub['installments'][0]['id']}", headers=ctx.admin, json={"paid_on": "2026-10-05"})
    report = ctx.client.get("/reports/taxes?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    sources = {row["key"]: row for row in report["by_source"]}
    assert sources["subscription"]["tax_cents"] == 856
    assert sources["subscription"]["services"] == 1
    csv_response = ctx.client.get("/reports/taxes/ledger.csv?start=2026-10-01&end=2026-10-31", headers=ctx.admin)
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    lines = csv_response.text.strip().splitlines()
    assert lines[0].startswith("date;source;code;tax;level;state;city;ibge_code;rate_percent;base_cents;tax_cents")
    assert len(lines) == 4  # header + PIS + COFINS + ISS
    assert any(line.startswith("2026-10-05;subscription;ISS;ISS;municipal;SP;Sample;3550308;5.00;9900;495;9900") for line in lines)


def test_finance_report(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book(marked_at=MONDAY_8, status="realizada")
    ctx.book(marked_at=MONDAY_9)  # scheduled: not earned yet
    report = ctx.client.get("/reports/finance?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    assert report["company"]["name"] == "Hollic Health Ltda"
    totals = report["totals"]
    assert totals["appointments_completed"] == 1
    assert totals["appointments_gross_cents"] == 15000
    assert totals["appointments_discount_cents"] == 7500
    assert totals["appointments_amount_cents"] == 7500
    assert totals["revenue_cents"] == 7500 + totals["plan_revenue_cents"]
    assert totals["tax_cents"] >= 649
    assert totals["net_cents"] == totals["revenue_cents"] - totals["tax_cents"]
    assert [p["period"] for p in report["periods"]] == ["2026-10"]


@pytest.mark.parametrize("path", ["/reports/taxes", "/reports/finance", "/reports/taxes/ledger.csv"])
@pytest.mark.parametrize("role", ["supervisor", "attendant", "client"])
def test_tax_and_finance_reports_are_admin_only(ctx, path, role):
    headers = ctx.client_headers if role == "client" else {"supervisor": ctx.supervisor, "attendant": ctx.attendant}[role]
    assert ctx.client.get(path, headers=headers).status_code == 403


# --- dashboard ---------------------------------------------------------------------------------


def test_dashboard_for_admin_has_money_and_queue(ctx):
    standard_rules(ctx)
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    # A completed appointment this month and a scheduled one in the next 7 days.
    ctx.book(marked_at=datetime.combine(TODAY, datetime.min.time()).replace(hour=8), status="realizada") if TODAY.weekday() < 5 else None
    soon = datetime.combine(TODAY + timedelta(days=1), datetime.min.time()).replace(hour=9)
    while soon.weekday() >= 5:
        soon += timedelta(days=1)
    ctx.book(marked_at=soon)
    data = ctx.client.get("/dashboard", headers=ctx.admin).json()
    assert data["role"] == "admin"
    assert data["today"] == TODAY.isoformat()
    assert data["appointments_upcoming_week"] >= 1
    assert data["next_appointments"][0]["patient"] == "Foo Holder"
    assert data["subscriptions_active"] == 1
    assert data["revenue_month_cents"] is not None
    assert data["revenue_month_cents"] >= data["plan_revenue_month_cents"]
    assert data["taxes_month_cents"] is not None
    assert data["net_month_cents"] == data["revenue_month_cents"] - data["taxes_month_cents"]
    assert data["queue_pending"] is not None and data["queue_dead"] == 0
    assert data["plan"] is None


def test_dashboard_for_staff_hides_money(ctx):
    for headers in (ctx.supervisor, ctx.attendant):
        data = ctx.client.get("/dashboard", headers=headers).json()
        assert data["revenue_month_cents"] is None
        assert data["queue_pending"] is None
        assert "appointments_today" in data


def test_dashboard_for_client_is_scoped_and_has_the_plan(ctx):
    other = ctx.client.post("/customers", headers=ctx.admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.admin, other, plan=ctx.plan)
    ctx.client.post(
        "/appointments", headers=ctx.admin, json={"customer_id": other, "professional_id": ctx.pro_foo, "marked_at": MONDAY_8.isoformat()}
    )
    data = ctx.client.get("/dashboard", headers=ctx.client_headers).json()
    assert data["role"] == "client"
    assert data["next_appointments"] == []
    assert data["subscriptions_active"] == 0
    assert data["plan"] is None
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book(marked_at=MONDAY_9)
    data = ctx.client.get("/dashboard", headers=ctx.client_headers).json()
    assert data["plan"]["status"] == "ativa"
    assert [row["patient"] for row in data["next_appointments"]] == ["Foo Holder"] or data["next_appointments"] == []
    assert data["revenue_month_cents"] is None
    assert data["unread_notifications"] >= 1

"""API tests for seller teams of any depth (seller -> coordinator ->
regional manager...): the region's override rates by level, snapshotted on
each sale for every ancestor, what a coordinator's login sees, and the
team notifications."""

from datetime import date

import pytest

from conftest import auth, auth_as, ensure_plan

JOINED = date(2026, 10, 1)
EXPIRES = date(2027, 9, 30)
MONTH = "granularity=month&start=2026-10-01&end=2026-10-31"


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.outbox = outbox
    c.plan = ensure_plan(client, admin)  # 99.00/month; 10% off below -> 89.10
    c.region = client.post(
        "/regions",
        headers=admin,
        json={
            "name": "Interior SP",
            "commission_bp": 1000,
            "areas": [{"state": "SP"}],
            "plans": [{"modality_id": c.plan["id"], "discount_percent": 10}],
            "override_bps": [300, 100],  # 3% to the direct leader, 1% to the leader's leader
        },
    ).json()
    c.rio = client.post("/regions", headers=admin, json={"name": "Rio", "commission_bp": 800, "areas": [{"state": "RJ"}]}).json()

    def seller(name, region=None, expect=201, **extra):
        response = client.post("/sellers", headers=admin, json={"name": name, "region_id": (region or c.region)["id"], **extra})
        assert response.status_code == expect, response.text
        return response.json()

    c.boss = seller("Rita Manager")  # regional manager
    c.lead = seller("Lia Leader", leader_id=c.boss["id"])  # coordinator reporting to Rita
    c.ana = seller("Ana Seller", leader_id=c.lead["id"])
    c.bia = seller("Bia Seller", leader_id=c.lead["id"])
    c.solo = seller("Solo Seller")
    c.seller = seller

    def customer(name="Foo Holder"):
        return client.post("/customers", headers=admin, json={"name": name, "city": "Campinas", "state": "SP"}).json()["id"]

    def sell(seller_id, customer_id=None, headers=None):
        body = {
            "customer_id": customer_id or customer(),
            "modality_id": c.plan["id"],
            "joined_on": JOINED.isoformat(),
            "expires_on": EXPIRES.isoformat(),
            "seller_id": seller_id,
        }
        response = client.post("/subscriptions", headers=headers or admin, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def pay(subscription, index, paid_on=JOINED):
        item = subscription["installments"][index]
        response = client.put(f"/subscriptions/{subscription['id']}/installments/{item['id']}", headers=admin, json={"paid_on": paid_on.isoformat()})
        assert response.status_code == 200, response.text
        return response.json()

    def update(seller_row, **changes):
        body = {key: seller_row[key] for key in ("name", "document", "email", "phone", "region_id", "active", "notes", "leader_id")}
        body.update(changes)
        return client.put(f"/sellers/{seller_row['id']}", headers=admin, json=body)

    c.customer = customer
    c.sell = sell
    c.pay = pay
    c.update = update
    return c


# --- the hierarchy ------------------------------------------------------------------------


def test_team_rules_same_region_any_depth_no_cycles(ctx):
    assert ctx.ana["leader_id"] == ctx.lead["id"] and ctx.ana["leader_name"] == "Lia Leader"
    assert ctx.region["override_bps"] == [300, 100] and ctx.region["override_labels"] == ["3%", "1%"]
    lead = ctx.client.get(f"/sellers/{ctx.lead['id']}", headers=ctx.admin).json()
    assert (lead["team_size"], lead["organisation_size"], lead["depth"], lead["leader_name"]) == (2, 2, 1, "Rita Manager")
    boss = ctx.client.get(f"/sellers/{ctx.boss['id']}", headers=ctx.admin).json()
    assert (boss["team_size"], boss["organisation_size"], boss["depth"]) == (1, 3, 0)
    assert ctx.client.get(f"/sellers/{ctx.ana['id']}", headers=ctx.admin).json()["depth"] == 2
    team = ctx.client.get(f"/sellers/{ctx.lead['id']}/team", headers=ctx.admin).json()
    assert [row["name"] for row in team] == ["Ana Seller", "Bia Seller"]
    # Another region's seller cannot lead; no cycles; a leader cannot leave its
    # team's region; nobody leads itself. Any depth is fine (Ana may lead Solo).
    carioca = ctx.seller("Carla Carioca", region=ctx.rio)
    assert ctx.update(ctx.solo, leader_id=carioca["id"]).status_code == 409
    assert ctx.update(ctx.solo, leader_id=ctx.ana["id"]).status_code == 200  # four levels: Rita > Lia > Ana > Solo
    assert ctx.client.get(f"/sellers/{ctx.boss['id']}", headers=ctx.admin).json()["organisation_size"] == 4
    assert ctx.update(ctx.boss, leader_id=ctx.solo["id"]).status_code == 409  # a cycle
    assert ctx.update(ctx.boss, leader_id=ctx.lead["id"]).status_code == 409
    assert ctx.update(ctx.lead, region_id=ctx.rio["id"]).status_code == 409
    assert ctx.update(ctx.lead, leader_id=ctx.lead["id"]).status_code == 400
    assert ctx.update(ctx.solo, leader_id=999).status_code == 404
    assert ctx.client.delete(f"/sellers/{ctx.lead['id']}", headers=ctx.admin).status_code == 409
    # A seller with a team moves region only after its team is reassigned; a
    # collaborator only by leaving its leader.
    assert ctx.update(ctx.bia, region_id=ctx.rio["id"]).status_code == 409
    moved = ctx.update(ctx.bia, region_id=ctx.rio["id"], leader_id=None)
    assert moved.status_code == 200 and moved.json()["leader_id"] is None
    assert ctx.client.get(f"/sellers/{ctx.lead['id']}", headers=ctx.admin).json()["team_size"] == 1
    # Override rates are the region's: trailing zero levels vanish, more than five are refused.
    assert ctx.client.put(f"/regions/{ctx.region['id']}", headers=ctx.admin, json={**ctx.region, "override_bps": [300, 0, 0]}).json()["override_bps"] == [300]
    assert ctx.client.put(f"/regions/{ctx.region['id']}", headers=ctx.admin, json={**ctx.region, "override_bps": [1] * 6}).status_code == 422


# --- the override --------------------------------------------------------------------------


def test_override_is_snapshotted_per_level_and_paid_on_fees_received(ctx):
    sold = ctx.sell(ctx.ana["id"])
    assert [(o["level"], o["seller_name"], o["rate_bp"]) for o in sold["overrides"]] == [(1, "Lia Leader", 300), (2, "Rita Manager", 100)]
    assert (sold["commission_bp"], sold["monthly_amount_cents"]) == (1000, 8910)  # Ana's side unchanged
    assert ctx.sell(ctx.solo["id"])["overrides"] == []
    lead_sale = ctx.sell(ctx.lead["id"])  # the coordinator's own sale: Rita is its level 1
    assert [(o["level"], o["seller_name"], o["rate_bp"]) for o in lead_sale["overrides"]] == [(1, "Rita Manager", 300)]
    assert ctx.sell(ctx.boss["id"])["overrides"] == []
    # Later changes to the region's rates or to the team never rewrite the sale.
    ctx.client.put(f"/regions/{ctx.region['id']}", headers=ctx.admin, json={**ctx.region, "override_bps": [1000]})
    assert ctx.update(ctx.ana, leader_id=None).status_code == 200
    sold = ctx.pay(sold, 0)
    sold = ctx.pay(sold, 1, date(2026, 10, 20))
    assert sold["commission_earned_cents"] == 2 * 891
    assert [(o["seller_name"], o["earned_cents"]) for o in sold["overrides"]] == [("Lia Leader", 2 * 267), ("Rita Manager", 2 * 89)]  # 3% and 1% of 89.10
    # New sales of Ana pay no override any more (she left the team).
    assert ctx.sell(ctx.ana["id"])["overrides"] == []
    # An inactive ancestor gets no row; the levels above it still do (level counted from the seller).
    assert ctx.update(ctx.lead, active=False).status_code == 200
    ctx.client.put(f"/regions/{ctx.region['id']}", headers=ctx.admin, json={**ctx.region, "override_bps": [300, 100]})
    bia = ctx.sell(ctx.bia["id"])
    assert [(o["level"], o["seller_name"], o["rate_bp"]) for o in bia["overrides"]] == [(2, "Rita Manager", 100)]


def test_clients_never_see_the_override(ctx):
    holder = ctx.customer()
    sold = ctx.sell(ctx.ana["id"], holder)
    body = ctx.client.get(f"/subscriptions/{sold['id']}", headers=auth_as(ctx.client, "client", customer_id=holder)).json()
    assert body["overrides"] == []
    assert ctx.client.get(f"/subscriptions/{sold['id']}", headers=ctx.admin).json()["overrides"] != []


# --- reports -------------------------------------------------------------------------------


def test_commission_report_shows_both_sides_of_each_seller(ctx):
    ana_sale = ctx.pay(ctx.sell(ctx.ana["id"]), 0)
    bia_sale = ctx.pay(ctx.sell(ctx.bia["id"]), 0)
    lead_sale = ctx.pay(ctx.sell(ctx.lead["id"]), 0)
    ctx.pay(ctx.sell(ctx.solo["id"]), 0)
    report = ctx.client.get(f"/reports/commissions?{MONTH}", headers=ctx.admin).json()
    # Company's cost: four commissions; overrides: Lia 3% on Ana's and Bia's,
    # Rita 1% on those two and 3% on Lia's own sale.
    assert report["totals"]["commission_cents"] == 4 * 891
    assert report["totals"]["override_cents"] == 2 * 267 + 2 * 89 + 267
    assert report["totals"]["total_cents"] == 4 * 891 + 2 * 267 + 2 * 89 + 267
    assert (report["totals"]["team_sold"], report["totals"]["team_payments"], report["totals"]["team_revenue_cents"]) == (3, 3, 3 * 8910)
    rows = {row["name"]: row for row in report["by_seller"]}
    lead = rows["Lia Leader"]
    assert (lead["sold"], lead["commission_cents"]) == (1, 891)  # her own sale
    assert (lead["team_size"], lead["organisation_size"], lead["team_sold"], lead["team_payments"], lead["override_cents"]) == (2, 2, 2, 2, 534)
    assert lead["total_cents"] == 891 + 534
    assert lead["leader_override_cents"] == 267  # her own sale paid Rita 3%
    boss = rows["Rita Manager"]
    assert (boss["sold"], boss["commission_cents"], boss["team_sold"], boss["override_cents"]) == (0, 0, 3, 2 * 89 + 267)
    assert (boss["team_size"], boss["organisation_size"]) == (1, 3)
    assert (rows["Ana Seller"]["commission_cents"], rows["Ana Seller"]["override_cents"], rows["Ana Seller"]["leader_name"]) == (891, 0, "Lia Leader")
    assert rows["Ana Seller"]["leader_override_cents"] == 267 + 89  # what her sale paid her two ancestors
    assert rows["Solo Seller"]["leader_name"] is None
    assert report["by_seller"][0]["name"] == "Lia Leader"  # ordered by total earnings
    # One seller's report is that seller's earnings.
    mine = ctx.client.get(f"/reports/commissions?{MONTH}&seller_id={ctx.lead['id']}", headers=ctx.admin).json()
    assert (mine["totals"]["commission_cents"], mine["totals"]["override_cents"], mine["totals"]["total_cents"]) == (891, 534, 1425)
    assert mine["portfolio"]["subscriptions"] == 1  # her own sales
    # The statement: her own line and two override lines; Rita's has three override lines with the level.
    statement = ctx.client.get(f"/reports/commissions/{ctx.lead['id']}?start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    assert (statement["commission_cents"], statement["override_cents"], statement["total_cents"]) == (891, 534, 1425)
    assert sorted((line["kind"], line["level"], line["sold_by"], line["commission_bp"], line["commission_cents"]) for line in statement["lines"]) == [
        ("commission", 0, "Lia Leader", 1000, 891),
        ("override", 1, "Ana Seller", 300, 267),
        ("override", 1, "Bia Seller", 300, 267),
    ]
    assert {line["subscription_id"] for line in statement["lines"]} == {ana_sale["id"], bia_sale["id"], lead_sale["id"]}
    rita = ctx.client.get(f"/reports/commissions/{ctx.boss['id']}?start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    assert sorted((line["level"], line["sold_by"], line["commission_cents"]) for line in rita["lines"]) == [(1, "Lia Leader", 267), (2, "Ana Seller", 89), (2, "Bia Seller", 89)]


def test_coordinator_login_sees_itself_and_its_team(ctx):
    lead = auth_as(ctx.client, "seller", seller_id=ctx.lead["id"])
    ana = auth_as(ctx.client, "seller", seller_id=ctx.ana["id"])
    ctx.pay(ctx.sell(ctx.ana["id"]), 0)
    ctx.pay(ctx.sell(ctx.lead["id"]), 0)
    ctx.pay(ctx.sell(ctx.solo["id"]), 0)
    me = ctx.client.get("/auth/me", headers=lead).json()
    assert me["seller_team_size"] == 2
    mine = ctx.client.get("/sellers/me", headers=lead).json()
    assert [row["name"] for row in mine["team"]] == ["Ana Seller", "Bia Seller"]
    assert mine["leader"]["name"] == "Rita Manager" and mine["team_size"] == 2 and mine["override_bps"] == [300, 100]
    assert ctx.client.get("/sellers/me", headers=ana).json()["leader"]["name"] == "Lia Leader"
    boss = auth_as(ctx.client, "seller", seller_id=ctx.boss["id"])
    organisation = ctx.client.get("/sellers/me", headers=boss).json()["organisation"]
    assert [(row["name"], row["depth"], row["leader_name"]) for row in organisation] == [
        ("Lia Leader", 1, "Rita Manager"),
        ("Ana Seller", 2, "Lia Leader"),
        ("Bia Seller", 2, "Lia Leader"),
    ]
    assert ctx.client.get("/auth/me", headers=boss).json()["seller_team_size"] == 3

    report = ctx.client.get(f"/reports/commissions?{MONTH}", headers=lead).json()
    assert (report["totals"]["commission_cents"], report["totals"]["override_cents"], report["totals"]["total_cents"]) == (891, 267, 1158)
    assert sorted(row["name"] for row in report["by_seller"]) == ["Ana Seller", "Bia Seller", "Lia Leader"]  # never Solo nor Rita
    # The regional manager sees the whole organisation, with its level-2 override.
    top = ctx.client.get(f"/reports/commissions?{MONTH}", headers=boss).json()
    assert sorted(row["name"] for row in top["by_seller"]) == ["Ana Seller", "Bia Seller", "Lia Leader", "Rita Manager"]
    assert (top["totals"]["commission_cents"], top["totals"]["override_cents"]) == (0, 89 + 267)
    assert ctx.client.get(f"/reports/commissions?{MONTH}&seller_id={ctx.ana['id']}", headers=boss).status_code == 200
    rows = {row["name"]: row for row in report["by_seller"]}
    assert (rows["Ana Seller"]["commission_cents"], rows["Ana Seller"]["payments"]) == (891, 1)
    assert (rows["Bia Seller"]["commission_cents"], rows["Bia Seller"]["sold"]) == (0, 0)
    # A collaborator's statement and report are open to its leader, not others'.
    assert ctx.client.get(f"/reports/commissions?{MONTH}&seller_id={ctx.ana['id']}", headers=lead).status_code == 200
    assert ctx.client.get(f"/reports/commissions/{ctx.ana['id']}?{MONTH}", headers=lead).status_code == 200
    assert ctx.client.get(f"/reports/commissions?{MONTH}&seller_id={ctx.solo['id']}", headers=lead).status_code == 403
    # A collaborator sees only itself.
    own = ctx.client.get(f"/reports/commissions?{MONTH}", headers=ana).json()
    assert [row["name"] for row in own["by_seller"]] == ["Ana Seller"]
    assert (own["totals"]["commission_cents"], own["totals"]["override_cents"]) == (891, 0)
    assert ctx.client.get(f"/reports/commissions?{MONTH}&seller_id={ctx.lead['id']}", headers=ana).status_code == 403

    # The team's sales: the leader follows them (read), the collaborator changes its own.
    listed = ctx.client.get("/subscriptions", headers=lead).json()
    assert sorted(row["seller_name"] for row in listed) == ["Ana Seller", "Lia Leader"]
    ana_sale = next(row for row in listed if row["seller_name"] == "Ana Seller")
    assert ctx.client.get(f"/subscriptions/{ana_sale['id']}", headers=lead).status_code == 200
    assert ctx.client.post(f"/subscriptions/{ana_sale['id']}/changes", headers=lead, json={"kind": "cancel"}).status_code == 403
    assert ctx.client.post(f"/subscriptions/{ana_sale['id']}/changes", headers=ana, json={"kind": "cancel"}).status_code == 201
    assert [row["seller_name"] for row in ctx.client.get("/subscriptions", headers=ana).json()] == ["Ana Seller"]

    home = ctx.client.get("/dashboard", headers=lead).json()
    assert (home["team_size"], home["team_sales_month"]) == (2, 1)
    assert (home["override_month_cents"], home["override_total_cents"]) == (267, 267)
    assert ctx.client.get("/dashboard", headers=boss).json()["team_size"] == 3
    assert ctx.client.get("/dashboard", headers=boss).json()["override_total_cents"] == 89 + 267
    assert (home["commission_month_cents"], home["sales_month"]) == (891, 1)
    assert sorted(row["seller_name"] or "-" for row in home["recent_sales"]) == ["-", "Ana Seller"]
    assert ctx.client.get("/dashboard", headers=ana).json()["leader_name"] == "Lia Leader"


# --- notifications -------------------------------------------------------------------------


def test_team_notifications(ctx):
    lead = auth_as(ctx.client, "seller", seller_id=ctx.lead["id"])
    ana = auth_as(ctx.client, "seller", seller_id=ctx.ana["id"])
    solo = auth_as(ctx.client, "seller", seller_id=ctx.solo["id"])
    boss = auth_as(ctx.client, "seller", seller_id=ctx.boss["id"])
    sold = ctx.sell(ctx.ana["id"], headers=ana)
    latest = ctx.client.get("/notifications", headers=lead).json()[0]
    assert latest["event"] == "team.sale_recorded"
    assert latest["title"] == "Team sale: Ana Seller sold Plan Foo to Foo Holder"
    assert "Your override (level 1): 3% on every installment paid" in latest["body"]
    assert "Your override (level 2): 1%" in ctx.client.get("/notifications", headers=boss).json()[0]["body"]
    ctx.pay(sold, 0)
    latest = ctx.client.get("/notifications", headers=lead).json()[0]
    assert latest["event"] == "override.earned"
    assert latest["title"] == "Override earned: R$ 2,67 — Foo Holder, sold by Ana Seller"
    assert ctx.client.get("/notifications", headers=boss).json()[0]["title"] == "Override earned: R$ 0,89 — Foo Holder, sold by Ana Seller"
    assert ctx.client.get("/notifications", headers=ana).json()[0]["event"] == "commission.earned"
    # Joining and leaving a team: both sides are told.
    assert ctx.update(ctx.solo, leader_id=ctx.lead["id"]).status_code == 200
    assert ctx.client.get("/notifications", headers=solo).json()[0]["title"] == "You now report to Lia Leader"
    assert ctx.client.get("/notifications", headers=lead).json()[0]["title"] == "Solo Seller joined your team"
    assert ctx.update(ctx.solo, leader_id=None).status_code == 200
    assert ctx.client.get("/notifications", headers=solo).json()[0]["title"] == "You no longer report to Lia Leader"
    left = ctx.client.get("/notifications", headers=lead).json()[0]
    assert left["title"] == "Solo Seller left your team" and "keep paying your override" in left["body"]

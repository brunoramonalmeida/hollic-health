"""API tests for sales regions, sellers and the regional rules of a plan
sale: plans available, special discounts, scope and commission."""

from datetime import date

import pytest

from app import clock
from conftest import auth, auth_as, ensure_plan

JOINED = date(2026, 10, 1)
EXPIRES = date(2027, 9, 30)


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers)  # 99.00/month, 2 dependents included
    c.other_plan = ensure_plan(client, headers, name="Plan Bar", plan_value_cents=19900)

    def region(name="Interior SP", areas=None, plans=None, commission_bp=1000, headers=None, expect=201):
        response = client.post(
            "/regions",
            headers=headers or c.headers,
            json={
                "name": name,
                "commission_bp": commission_bp,
                "areas": areas if areas is not None else [{"state": "SP", "city": "Campinas"}, {"state": "MG"}],
                "plans": plans if plans is not None else [{"modality_id": c.plan["id"], "discount_percent": 10}],
            },
        )
        assert response.status_code == expect, response.text
        return response.json()

    def seller(region_id, name="Ana Seller", active=True, headers=None, expect=201):
        response = client.post(
            "/sellers",
            headers=headers or c.headers,
            json={"name": name, "region_id": region_id, "active": active, "email": "ana@example.com"},
        )
        assert response.status_code == expect, response.text
        return response.json()

    def customer(name="Foo Holder", city="Campinas", state="SP"):
        response = client.post("/customers", headers=c.headers, json={"name": name, "city": city, "state": state})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def sell(customer_id, seller_id=None, plan=None, headers=None, omit_seller=False):
        body = {
            "customer_id": customer_id,
            "modality_id": (plan or c.plan)["id"],
            "joined_on": JOINED.isoformat(),
            "expires_on": EXPIRES.isoformat(),
        }
        if not omit_seller:
            body["seller_id"] = seller_id
        return client.post("/subscriptions", headers=headers or c.headers, json=body)

    def edit(subscription, headers=None, **changes):
        body = {
            "customer_id": subscription["customer_id"],
            "modality_id": subscription["modality_id"],
            "joined_on": subscription["joined_on"],
            "expires_on": subscription["expires_on"],
            **changes,
        }
        return client.put(f"/subscriptions/{subscription['id']}", headers=headers or c.headers, json=body)

    def pay(subscription, index, paid_on):
        item = subscription["installments"][index]
        response = client.put(
            f"/subscriptions/{subscription['id']}/installments/{item['id']}",
            headers=c.headers,
            json={"paid_on": paid_on.isoformat()},
        )
        assert response.status_code == 200, response.text
        return response.json()

    c.region = region
    c.seller = seller
    c.customer = customer
    c.sell = sell
    c.edit = edit
    c.pay = pay
    return c


# --- regions ------------------------------------------------------------------------


def test_region_carries_commission_scope_and_plans_with_special_discount(ctx):
    region = ctx.region(commission_bp=1250)
    assert region["name"] == "Interior SP"
    assert region["commission_bp"] == 1250
    assert region["commission_label"] == "12.5%"
    assert [area["label"] for area in region["areas"]] == ["MG", "Campinas/SP"]
    assert region["scope"] == "MG, Campinas/SP"
    assert region["plans"] == [
        {
            "modality_id": ctx.plan["id"],
            "discount_percent": 10,
            "modality_name": "Plan Foo",
            "plan_value_cents": 9900,
            "value_cents": 8910,
        }
    ]
    assert region["sellers_count"] == 0
    assert ctx.client.get(f"/regions/{region['id']}", headers=ctx.headers).json() == region


def test_region_validation(ctx):
    ctx.region()
    ctx.region(name="interior sp", expect=409)  # names are unique, ignoring case
    ctx.region(name="No scope", areas=[], expect=422)  # a region must say where it sells
    ctx.region(name="Bad state", areas=[{"state": "XX"}], expect=422)
    ctx.region(
        name="Repeated",
        areas=[{"state": "SP", "city": "São Paulo"}, {"state": "sp", "city": " sao  paulo "}],
        expect=422,
    )
    ctx.region(
        name="Repeated plan",
        plans=[{"modality_id": ctx.plan["id"]}, {"modality_id": ctx.plan["id"], "discount_percent": 5}],
        expect=422,
    )
    ctx.region(name="Bad discount", plans=[{"modality_id": ctx.plan["id"], "discount_percent": 101}], expect=422)
    ctx.region(name="Bad commission", commission_bp=10001, expect=422)
    ctx.region(name="Unknown plan", plans=[{"modality_id": 999}], expect=404)


def test_region_update_replaces_scope_and_plans(ctx):
    region = ctx.region()
    response = ctx.client.put(
        f"/regions/{region['id']}",
        headers=ctx.headers,
        json={
            "name": "Interior",
            "commission_bp": 800,
            "areas": [{"state": "SP", "city": "Campinas"}, {"state": "RJ", "city": "Niterói"}],
            "plans": [{"modality_id": ctx.other_plan["id"], "discount_percent": 0}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Interior"
    assert body["scope"] == "Niterói/RJ, Campinas/SP"  # by state, then city
    assert [item["modality_name"] for item in body["plans"]] == ["Plan Bar"]
    assert body["plans"][0]["value_cents"] == 19900


def test_staff_reads_regions_and_sellers_only_managers_write(ctx):
    region = ctx.region()
    attendant = auth_as(ctx.client, "attendant")
    supervisor = auth_as(ctx.client, "supervisor")
    assert ctx.client.get("/regions", headers=attendant).status_code == 200
    assert ctx.client.get("/sellers", headers=attendant).status_code == 200
    ctx.region(name="By attendant", headers=attendant, expect=403)
    ctx.seller(region["id"], headers=attendant, expect=403)
    ctx.region(name="By supervisor", headers=supervisor)
    ctx.seller(region["id"], headers=supervisor)
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.customer())
    assert ctx.client.get("/regions", headers=client_headers).status_code == 403
    assert ctx.client.get("/sellers", headers=client_headers).status_code == 403


# --- sellers ------------------------------------------------------------------------


def test_seller_must_belong_to_a_region(ctx):
    missing = ctx.client.post("/sellers", headers=ctx.headers, json={"name": "No region"})
    assert missing.status_code == 422
    ctx.seller(999, expect=404)
    region = ctx.region()
    seller = ctx.seller(region["id"])
    assert seller["region_id"] == region["id"]
    assert seller["region_name"] == "Interior SP"
    assert seller["commission_bp"] == 1000
    assert seller["active"] is True
    listed = ctx.client.get(f"/sellers?region_id={region['id']}", headers=ctx.headers).json()
    assert [item["id"] for item in listed] == [seller["id"]]
    region_after = ctx.client.get(f"/regions/{region['id']}", headers=ctx.headers).json()
    assert (region_after["sellers_count"], region_after["active_sellers_count"]) == (1, 1)


def test_region_with_sellers_and_seller_with_sales_cannot_be_deleted(ctx):
    region = ctx.region()
    seller = ctx.seller(region["id"])
    assert ctx.client.delete(f"/regions/{region['id']}", headers=ctx.headers).status_code == 409
    assert ctx.sell(ctx.customer(), seller["id"]).status_code == 201
    response = ctx.client.delete(f"/sellers/{seller['id']}", headers=ctx.headers)
    assert response.status_code == 409
    assert "deactivate" in response.json()["detail"]
    # A seller without sales and an empty region can go.
    spare = ctx.seller(region["id"], name="Spare")
    assert ctx.client.delete(f"/sellers/{spare['id']}", headers=ctx.headers).status_code == 204
    empty = ctx.region(name="Empty")
    assert ctx.client.delete(f"/regions/{empty['id']}", headers=ctx.headers).status_code == 204


# --- the sale -------------------------------------------------------------------------


def test_sale_inside_the_region_snapshots_terms_and_applies_the_special_discount(ctx):
    region = ctx.region(commission_bp=1250)
    seller = ctx.seller(region["id"])
    response = ctx.sell(ctx.customer(), seller["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["seller_id"] == seller["id"]
    assert body["seller_name"] == "Ana Seller"
    assert body["region_id"] == region["id"]
    assert body["region_name"] == "Interior SP"
    assert body["commission_bp"] == 1250
    assert body["discount_percent"] == 10
    assert body["list_amount_cents"] == 9900
    assert body["monthly_amount_cents"] == 8910
    assert {item["amount_cents"] for item in body["installments"]} == {8910}
    assert {item["discount_cents"] for item in body["installments"]} == {990}
    assert body["commission_earned_cents"] == 0


def test_seller_cannot_sell_a_plan_its_region_does_not_offer(ctx):
    seller = ctx.seller(ctx.region()["id"])
    response = ctx.sell(ctx.customer(), seller["id"], plan=ctx.other_plan)
    assert response.status_code == 409
    assert response.json()["detail"] == 'Plan "Plan Bar" is not sold in region Interior SP (seller Ana Seller)'


def test_seller_cannot_sell_outside_the_scope_of_its_region(ctx):
    seller = ctx.seller(ctx.region()["id"])
    outside = ctx.sell(ctx.customer(city="Rio de Janeiro", state="RJ"), seller["id"])
    assert outside.status_code == 409
    assert "(Rio de Janeiro/RJ) is outside region Interior SP" in outside.json()["detail"]
    # Another city of the same state is outside a city area.
    assert ctx.sell(ctx.customer(name="Other city", city="Santos"), seller["id"]).status_code == 409
    # No address: the scope cannot be checked.
    no_address = ctx.sell(ctx.customer(name="Nowhere", city="", state=""), seller["id"])
    assert no_address.status_code == 409
    assert "no city/state" in no_address.json()["detail"]
    # Whole-state area: any city of MG. City names ignore accents and case.
    assert ctx.sell(ctx.customer(name="Mineiro", city="Belo Horizonte", state="MG"), seller["id"]).status_code == 201
    assert ctx.sell(ctx.customer(name="Campineiro", city="  CAMPINAS ", state="sp"), seller["id"]).status_code == 201


def test_inactive_or_unknown_seller_cannot_sell(ctx):
    region = ctx.region()
    inactive = ctx.seller(region["id"], name="Former", active=False)
    response = ctx.sell(ctx.customer(), inactive["id"])
    assert response.status_code == 409
    assert "inactive" in response.json()["detail"]
    assert ctx.sell(ctx.customer(name="Other"), 999).status_code == 404


def test_sale_without_seller_is_not_bound_by_regions(ctx):
    ctx.region()
    response = ctx.sell(ctx.customer(city="Rio de Janeiro", state="RJ"), None, plan=ctx.other_plan)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["seller_id"] is None and body["region_id"] is None
    assert body["monthly_amount_cents"] == 19900
    assert body["commission_bp"] == 0


def test_only_the_staff_attributes_a_sale_to_a_seller(ctx):
    seller = ctx.seller(ctx.region()["id"])
    holder = ctx.customer()
    client_headers = auth_as(ctx.client, "client", customer_id=holder)
    refused = ctx.sell(holder, seller["id"], headers=client_headers)
    assert refused.status_code == 403
    quote = ctx.client.get(
        f"/subscriptions/quote?customer_id={holder}&modality_id={ctx.plan['id']}&seller_id={seller['id']}",
        headers=client_headers,
    )
    assert quote.status_code == 403
    # Sold by the seller (staff records it): the client edits the period and
    # the sale stays the seller's, with its discount.
    sold = ctx.sell(holder, seller["id"]).json()
    edited = ctx.edit(sold, headers=client_headers, expires_on="2027-06-30")
    assert edited.status_code == 200, edited.text
    assert edited.json()["seller_id"] == seller["id"]
    assert edited.json()["monthly_amount_cents"] == 8910
    # ...but cannot drop the seller or pick another one.
    other = ctx.seller(ctx.region(name="Other")["id"], name="Other Seller")
    assert ctx.edit(sold, headers=client_headers, seller_id=other["id"]).status_code == 403
    kept = ctx.edit(sold, headers=client_headers, seller_id=None)
    assert kept.status_code == 200 and kept.json()["seller_id"] == seller["id"]


def test_later_region_changes_never_rewrite_a_sale(ctx):
    region = ctx.region(commission_bp=1000)
    seller = ctx.seller(region["id"])
    sold = ctx.sell(ctx.customer(), seller["id"]).json()
    # The region drops the plan, changes its commission and leaves Campinas.
    changed = ctx.client.put(
        f"/regions/{region['id']}",
        headers=ctx.headers,
        json={"name": "Interior SP", "commission_bp": 300, "areas": [{"state": "MG"}], "plans": []},
    )
    assert changed.status_code == 200
    # Editing the dates keeps the terms sold (not re-checked).
    edited = ctx.edit(sold, expires_on="2027-03-31")
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["commission_bp"], body["discount_percent"], body["monthly_amount_cents"]) == (1000, 10, 8910)
    # Changing the plan is a new sale: checked against the region as it is now.
    response = ctx.edit(sold, modality_id=ctx.other_plan["id"])
    assert response.status_code == 409
    assert "not sold in region" in response.json()["detail"]


def test_moving_a_seller_keeps_past_sales_in_their_region(ctx):
    interior = ctx.region()
    rio = ctx.region(name="Rio", areas=[{"state": "RJ"}], plans=[{"modality_id": ctx.other_plan["id"], "discount_percent": 5}], commission_bp=500)
    seller = ctx.seller(interior["id"])
    sold = ctx.sell(ctx.customer(), seller["id"]).json()
    moved = ctx.client.put(
        f"/sellers/{seller['id']}",
        headers=ctx.headers,
        json={"name": "Ana Seller", "region_id": rio["id"], "active": True},
    )
    assert moved.status_code == 200 and moved.json()["region_name"] == "Rio"
    # The old sale still belongs to the Interior region.
    again = ctx.client.get(f"/subscriptions/{sold['id']}", headers=ctx.headers).json()
    assert (again["region_name"], again["commission_bp"]) == ("Interior SP", 1000)
    # New sales are bound by the new region.
    assert ctx.sell(ctx.customer(name="Campinas 2"), seller["id"]).status_code == 409
    carioca = ctx.sell(ctx.customer(name="Carioca", city="Rio de Janeiro", state="RJ"), seller["id"], plan=ctx.other_plan)
    assert carioca.status_code == 201, carioca.text
    assert (carioca.json()["region_name"], carioca.json()["monthly_amount_cents"]) == ("Rio", 18905)


def test_removing_the_seller_reprices_open_installments_at_the_list_price(ctx):
    seller = ctx.seller(ctx.region()["id"])
    sold = ctx.sell(ctx.customer(), seller["id"]).json()
    sold = ctx.pay(sold, 0, JOINED)
    # Omitted on edit: the seller stays.
    kept = ctx.edit(sold)
    assert kept.json()["seller_id"] == seller["id"]
    # Explicit null: the sale is no longer the seller's.
    response = ctx.edit(sold, seller_id=None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["seller_id"] is None and body["region_id"] is None and body["discount_percent"] == 0
    first, second = body["installments"][0], body["installments"][1]
    assert (first["amount_cents"], first["paid_cents"]) == (8910, 8910)  # paid: untouched
    assert (second["amount_cents"], second["discount_cents"]) == (9900, 0)


def test_quote_previews_the_sale(ctx):
    seller = ctx.seller(ctx.region(commission_bp=1000)["id"])
    holder = ctx.customer()

    def quote(**params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        response = ctx.client.get(f"/subscriptions/quote?{query}", headers=ctx.headers)
        assert response.status_code == 200, response.text
        return response.json()

    body = quote(customer_id=holder, modality_id=ctx.plan["id"], seller_id=seller["id"])
    assert body["allowed"] is True and body["reason"] is None
    assert (body["list_amount_cents"], body["discount_percent"], body["monthly_amount_cents"]) == (9900, 10, 8910)
    assert (body["commission_bp"], body["commission_cents"]) == (1000, 891)
    assert body["region_name"] == "Interior SP"
    refused = quote(customer_id=holder, modality_id=ctx.other_plan["id"], seller_id=seller["id"])
    assert refused["allowed"] is False
    assert "not sold in region" in refused["reason"]
    assert refused["monthly_amount_cents"] == 19900
    direct = quote(customer_id=holder, modality_id=ctx.other_plan["id"])
    assert direct["allowed"] is True and direct["monthly_amount_cents"] == 19900


def test_subscriptions_filter_by_seller_and_region(ctx):
    region = ctx.region()
    seller = ctx.seller(region["id"])
    sold = ctx.sell(ctx.customer(), seller["id"]).json()
    ctx.sell(ctx.customer(name="Direct"), None)
    by_seller = ctx.client.get(f"/subscriptions?seller_id={seller['id']}", headers=ctx.headers).json()
    by_region = ctx.client.get(f"/subscriptions?region_id={region['id']}", headers=ctx.headers).json()
    assert [row["id"] for row in by_seller] == [sold["id"]]
    assert [row["id"] for row in by_region] == [sold["id"]]
    assert ctx.client.get(f"/sellers/{seller['id']}", headers=ctx.headers).json()["subscriptions_count"] == 1


# --- commissions ----------------------------------------------------------------------


def test_commission_report_pays_the_snapshotted_rate_on_fees_received(ctx):
    interior = ctx.region(commission_bp=1000)
    ana = ctx.seller(interior["id"])
    bia = ctx.seller(interior["id"], name="Bia Seller")
    sold = ctx.sell(ctx.customer(), ana["id"]).json()
    sold = ctx.pay(sold, 0, date(2026, 10, 1))
    sold = ctx.pay(sold, 1, date(2026, 10, 20))
    assert sold["commission_earned_cents"] == 2 * 891
    assert [item["commission_cents"] for item in sold["installments"][:3]] == [891, 891, 0]
    # A later rate change does not touch this sale.
    ctx.client.put(
        f"/regions/{interior['id']}",
        headers=ctx.headers,
        json={"name": "Interior SP", "commission_bp": 5000, "areas": [{"state": "SP"}], "plans": [{"modality_id": ctx.plan["id"]}]},
    )
    # A direct sale (no seller) pays no commission and is not in the report.
    direct = ctx.sell(ctx.customer(name="Direct"), None).json()
    ctx.pay(direct, 0, date(2026, 10, 1))

    response = ctx.client.get(
        "/reports/commissions?granularity=month&start=2026-09-01&end=2026-10-31", headers=ctx.headers
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["totals"]["sold"] == 1
    assert report["totals"]["payments"] == 2
    assert report["totals"]["revenue_cents"] == 2 * 8910
    assert report["totals"]["discount_cents"] == 2 * 990
    assert report["totals"]["commission_cents"] == 2 * 891
    assert [row["period"] for row in report["periods"]] == ["2026-09", "2026-10"]
    assert report["periods"][1]["commission_cents"] == 2 * 891
    rows = {row["name"]: row for row in report["by_seller"]}
    assert rows["Ana Seller"]["commission_cents"] == 1782
    assert rows["Ana Seller"]["region_name"] == "Interior SP"
    assert rows["Bia Seller"]["commission_cents"] == 0  # listed with nothing, so the control is complete
    assert bia["id"] == rows["Bia Seller"]["id"]
    assert [(row["name"], row["commission_cents"], row["sellers_count"]) for row in report["by_region"]] == [
        ("Interior SP", 1782, 2)
    ]

    statement = ctx.client.get(
        f"/reports/commissions/{ana['id']}?start=2026-10-01&end=2026-10-31", headers=ctx.headers
    ).json()
    assert statement["seller_name"] == "Ana Seller"
    assert statement["payments"] == 2
    assert statement["commission_cents"] == 1782
    assert [(line["sequence"], line["paid_cents"], line["commission_bp"], line["commission_cents"]) for line in statement["lines"]] == [
        (1, 8910, 1000, 891),
        (2, 8910, 1000, 891),
    ]


def test_commission_report_filters_and_access(ctx):
    interior = ctx.region()
    rio = ctx.region(name="Rio", areas=[{"state": "RJ"}], commission_bp=500)
    ana = ctx.seller(interior["id"])
    ctx.seller(rio["id"], name="Carla Seller")
    sold = ctx.sell(ctx.customer(), ana["id"]).json()
    ctx.pay(sold, 0, date(2026, 10, 1))
    query = "granularity=month&start=2026-10-01&end=2026-10-31"
    only_rio = ctx.client.get(f"/reports/commissions?{query}&region_id={rio['id']}", headers=ctx.headers).json()
    assert only_rio["totals"]["commission_cents"] == 0
    assert [row["name"] for row in only_rio["by_seller"]] == ["Carla Seller"]
    assert [row["name"] for row in only_rio["by_region"]] == ["Rio"]
    only_ana = ctx.client.get(f"/reports/commissions?{query}&seller_id={ana['id']}", headers=ctx.headers).json()
    assert [row["name"] for row in only_ana["by_seller"]] == ["Ana Seller"]
    assert [row["name"] for row in only_ana["by_region"]] == ["Interior SP"]
    assert only_ana["totals"]["commission_cents"] == 891
    for role in ("supervisor", "attendant"):
        headers = auth_as(ctx.client, role)
        assert ctx.client.get(f"/reports/commissions?{query}", headers=headers).status_code == 403
        assert ctx.client.get(f"/reports/commissions/{ana['id']}?{query}", headers=headers).status_code == 403
    assert ctx.client.get(f"/reports/commissions/999?{query}", headers=ctx.headers).status_code == 404


def test_clients_never_see_the_sellers_commission(ctx):
    seller = ctx.seller(ctx.region(commission_bp=1000)["id"])
    holder = ctx.customer()
    sold = ctx.sell(holder, seller["id"]).json()
    ctx.pay(sold, 0, JOINED)
    client_headers = auth_as(ctx.client, "client", customer_id=holder)
    for body in (
        ctx.client.get(f"/subscriptions/{sold['id']}", headers=client_headers).json(),
        ctx.client.get("/subscriptions", headers=client_headers).json()[0],
        ctx.client.get(f"/subscriptions/customer/{holder}", headers=client_headers).json(),
        ctx.client.get("/dashboard", headers=client_headers).json()["plan"],
    ):
        # The client sees who sold the plan and its own price...
        assert (body["seller_name"], body["discount_percent"], body["monthly_amount_cents"]) == ("Ana Seller", 10, 8910)
        # ...but not what the seller earns.
        assert body["commission_bp"] is None
        assert body["commission_earned_cents"] is None
        assert {item["commission_cents"] for item in body["installments"]} == {None}
    staff_view = ctx.client.get(f"/subscriptions/{sold['id']}", headers=ctx.headers).json()
    assert (staff_view["commission_bp"], staff_view["commission_earned_cents"]) == (1000, 891)

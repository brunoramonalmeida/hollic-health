"""API tests for plan discounts applied to appointments."""

from datetime import date, datetime

import pytest

from conftest import activate_plan, auth, ensure_plan
from app import clock

MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)
# Test catalog: clinic fee 2000, specialty "Clínico geral" 8000, professional 5000 -> gross 15000.
GROSS = 15000


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers, discount_percent=20)
    c.professional = client.post(
        "/professionals",
        headers=headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000},
    ).json()["id"]
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"}).json()["id"]

    def book(customer_id=None, marked_at=MONDAY_8, **overrides):
        payload = {
            "customer_id": customer_id or c.holder,
            "professional_id": c.professional,
            "marked_at": marked_at.isoformat(),
        }
        payload.update(overrides)
        return client.post("/appointments", headers=headers, json=payload)

    def set_discounts(discounts, plan=None):
        response = client.put(
            f"/modalities/{(plan or c.plan)['id']}/discounts", headers=headers, json={"discounts": discounts}
        )
        assert response.status_code == 200, response.text
        return response.json()

    c.book = book
    c.set_discounts = set_discounts
    return c


def test_plan_default_discount_is_applied(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    body = ctx.book().json()
    assert body["gross_cents"] == GROSS
    assert body["discount_percent"] == 20
    assert body["discount_cents"] == 3000
    assert body["amount_cents"] == 12000
    assert body["modality_id"] == ctx.plan["id"]
    assert body["modality_name"] == "Plan Foo"
    assert body["created_by_name"] == "Foo Bar"


def test_specialty_override_wins_over_plan_default(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    plan = ctx.set_discounts([{"specialty_id": 1, "percent": 50}])
    assert plan["discounts"] == [{"specialty_id": 1, "percent": 50, "specialty_name": "Clínico geral"}]
    body = ctx.book().json()
    assert body["discount_percent"] == 50
    assert body["amount_cents"] == 7500


def test_override_applies_only_to_its_specialty(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    other = ctx.client.post("/specialties", headers=ctx.headers, json={"name": "Specialty Bar", "price_cents": 1000}).json()
    pro = ctx.client.post(
        "/professionals",
        headers=ctx.headers,
        json={"name": "Dolor Sit", "specialty_id": other["id"], "clinic_id": 1, "price_cents": 1000},
    ).json()["id"]
    ctx.set_discounts([{"specialty_id": other["id"], "percent": 0}])
    discounted = ctx.book().json()
    assert discounted["discount_percent"] == 20  # plan default for Clínico geral
    none = ctx.book(professional_id=pro, marked_at=MONDAY_9).json()
    assert none["discount_percent"] == 0
    assert none["amount_cents"] == 2000 + 1000 + 1000


def test_quote_previews_the_discounted_price(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    quote = ctx.client.get(
        f"/appointments/quote?customer_id={ctx.holder}&professional_id={ctx.professional}", headers=ctx.headers
    )
    assert quote.status_code == 200
    assert quote.json() == {
        "on_call": False,  # regular hours: regular price list
        "urgent": False,
        "remote": False,  # in person: the default
        "fee_cents": 2000,
        "specialty_price_cents": 8000,
        "professional_price_cents": 5000,
        "gross_cents": GROSS,
        "modality_id": ctx.plan["id"],
        "modality_name": "Plan Foo",
        "discount_percent": 20,
        "discount_cents": 3000,
        "amount_cents": 12000,
        "tax_cents": 0,  # no tax rules in the test catalog
        "net_cents": 12000,
        "taxes": [],
        # Plan Foo has no appointment limit: unlimited, never over the limit.
        "over_limit": False,
        "plan_limit": 0,
        "plan_limit_period": "month",
        "plan_used": 0,
        "plan_remaining": None,
        "plan_window_start": quote.json()["plan_window_start"],  # today's billing period
        "plan_window_end": quote.json()["plan_window_end"],
    }
    assert quote.json()["plan_window_start"] <= clock.today().isoformat() <= quote.json()["plan_window_end"]


def test_quote_without_plan_has_no_discount(ctx):
    quote = ctx.client.get(
        f"/appointments/quote?customer_id={ctx.holder}&professional_id={ctx.professional}", headers=ctx.headers
    ).json()
    assert quote["modality_id"] is None
    assert quote["discount_percent"] == 0
    assert quote["amount_cents"] == GROSS


def test_discount_is_a_snapshot(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    item = ctx.book().json()
    # Plan default changes afterwards...
    updated = ctx.client.put(
        "/modalities/%d" % ctx.plan["id"],
        headers=ctx.headers,
        json={**ctx.plan, "discount_percent": 80},
    )
    assert updated.status_code == 200
    # ...editing notes keeps the booked price.
    edited = ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.headers,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": MONDAY_8.isoformat(), "notes": "x"},
    ).json()
    assert edited["discount_percent"] == 20
    assert edited["amount_cents"] == 12000
    # A new booking uses the new percentage.
    fresh = ctx.book(marked_at=MONDAY_9).json()
    assert fresh["discount_percent"] == 80
    assert fresh["amount_cents"] == 3000


def test_changing_customer_reprices_with_their_plan(ctx):
    activate_plan(ctx.client, ctx.headers, ctx.holder, plan=ctx.plan)
    premium = ensure_plan(ctx.client, ctx.headers, name="Plan Premium", discount_percent=60)
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.headers, other, plan=premium)
    item = ctx.book().json()
    moved = ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.headers,
        json={"customer_id": other, "professional_id": ctx.professional, "marked_at": MONDAY_8.isoformat()},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["modality_name"] == "Plan Premium"
    assert moved.json()["discount_percent"] == 60
    assert moved.json()["amount_cents"] == 6000


def test_cancelled_appointment_without_plan_has_no_discount(ctx):
    body = ctx.book(status="cancelada").json()
    assert body["modality_id"] is None
    assert body["discount_cents"] == 0
    assert body["amount_cents"] == GROSS


def test_discount_validation(ctx):
    assert ctx.client.put(
        f"/modalities/{ctx.plan['id']}/discounts", headers=ctx.headers, json={"discounts": [{"specialty_id": 1, "percent": 101}]}
    ).status_code == 422
    assert ctx.client.put(
        f"/modalities/{ctx.plan['id']}/discounts", headers=ctx.headers, json={"discounts": [{"specialty_id": 9999, "percent": 10}]}
    ).status_code == 404
    assert ctx.client.put(
        f"/modalities/{ctx.plan['id']}/discounts",
        headers=ctx.headers,
        json={"discounts": [{"specialty_id": 1, "percent": 10}, {"specialty_id": 1, "percent": 20}]},
    ).status_code == 400
    assert ctx.client.post(
        "/modalities", headers=ctx.headers, json={"name": "Bad", "discount_percent": -1}
    ).status_code == 422


def test_discounts_are_listed_with_the_plan(ctx):
    ctx.set_discounts([{"specialty_id": 1, "percent": 35}])
    listed = ctx.client.get("/modalities", headers=ctx.headers).json()
    plan = next(item for item in listed if item["id"] == ctx.plan["id"])
    assert plan["discount_percent"] == 20
    assert plan["discounts"][0]["percent"] == 35

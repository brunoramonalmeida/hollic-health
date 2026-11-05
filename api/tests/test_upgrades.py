"""API tests for plan upgrades: a new subscription replaces the customer's
current one, which ends the day before the new one starts; statuses,
installments, coverage of appointments, who may do it and notifications."""

from datetime import date, datetime

import pytest

from app import clock
from conftest import auth, auth_as, ensure_plan

TODAY = clock.today()  # 2026-10-22


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.outbox = outbox
    c.basic = ensure_plan(client, admin)  # Plan Foo, 99.00
    c.premium = ensure_plan(client, admin, name="Plan Premium", plan_value_cents=19900, discount_percent=40)
    c.holder = client.post("/customers", headers=admin, json={"name": "Foo Holder", "city": "Campinas", "state": "SP"}).json()["id"]
    c.professional = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]

    def subscribe(plan, joined_on, expires_on, headers=None, customer_id=None, **extra):
        return client.post(
            "/subscriptions",
            headers=headers or admin,
            json={
                "customer_id": customer_id or c.holder,
                "modality_id": plan["id"],
                "joined_on": joined_on.isoformat(),
                "expires_on": expires_on.isoformat(),
                **extra,
            },
        )

    def pay(subscription, index, paid_on=None):
        item = subscription["installments"][index]
        response = client.put(
            f"/subscriptions/{subscription['id']}/installments/{item['id']}",
            headers=admin,
            json={"paid_on": (paid_on or date.fromisoformat(item["due_on"])).isoformat()},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get(subscription_id, headers=None):
        return client.get(f"/subscriptions/{subscription_id}", headers=headers or admin).json()

    c.subscribe = subscribe
    c.pay = pay
    c.get = get
    return c


def test_a_customer_with_a_current_plan_needs_an_explicit_upgrade(ctx):
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    refused = ctx.subscribe(ctx.premium, date(2026, 10, 10), date(2027, 10, 9))
    assert refused.status_code == 409
    assert "upgrade it (upgrade=true)" in refused.json()["detail"]
    # The upgrade starts after the current plan's join date.
    too_early = ctx.subscribe(ctx.premium, date(2026, 1, 10), date(2027, 1, 9), upgrade=True)
    assert too_early.status_code == 409 and "starts after" in too_early.json()["detail"]
    assert ctx.get(first["id"])["upgraded_to_id"] is None


def test_upgrade_ends_the_current_plan_the_day_before_and_keeps_paid_installments(ctx):
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    for index in range(10):  # paid up to the installment of 10/10 - 09/11
        first = ctx.pay(first, index)
    assert first["status"] == "ativa"
    upgraded = ctx.subscribe(ctx.premium, date(2026, 11, 10), date(2027, 11, 9), upgrade=True)
    assert upgraded.status_code == 201, upgraded.text
    new = upgraded.json()
    assert new["upgraded_from_id"] == first["id"]
    assert new["modality_name"] == "Plan Premium"
    assert new["status"] == "pendente"  # starts later, first installment unpaid
    assert new["installments"][0]["due_on"] == "2026-11-10"
    old = ctx.get(first["id"])
    assert old["upgraded_to_id"] == new["id"]
    assert old["expires_on"] == "2026-11-09"  # the day before the new plan
    assert old["status"] == "ativa"  # still the plan in force today (the upgrade is ahead)
    assert old["installments_total"] == 10 and old["installments_paid"] == 10  # open ones after the end dropped, paid ones kept
    assert [item["period_end"] for item in old["installments"]][-1] == "2026-11-09"
    # The customer's current plan is the one in force today; once the new one starts, it is the new one.
    assert ctx.client.get(f"/subscriptions/customer/{ctx.holder}", headers=ctx.admin).json()["id"] == first["id"]
    clock.travel_to(datetime(2026, 11, 15, 10, 0))
    assert ctx.client.get(f"/subscriptions/customer/{ctx.holder}", headers=ctx.admin).json()["id"] == new["id"]
    assert ctx.get(first["id"])["status"] == "migrada"  # replaced, not merely expired


def test_upgrade_cannot_cut_a_paid_installment(ctx):
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    for index in range(10):
        first = ctx.pay(first, index)  # paid through 09/11
    # Ending before a paid installment starts would cut a period already paid for.
    refused = ctx.subscribe(ctx.premium, date(2026, 10, 5), date(2027, 10, 4), upgrade=True)
    assert refused.status_code == 409
    assert "paid installment starting 2026-10-10" in refused.json()["detail"]
    assert ctx.get(first["id"])["upgraded_to_id"] is None  # nothing changed
    assert len(ctx.client.get("/subscriptions", headers=ctx.admin).json()) == 1
    # Ending inside a paid period is fine: the paid installment stays (no proration).
    allowed = ctx.subscribe(ctx.premium, date(2026, 10, 25), date(2027, 10, 24), upgrade=True)
    assert allowed.status_code == 201, allowed.text
    old = ctx.get(first["id"])
    assert old["expires_on"] == "2026-10-24" and old["installments_paid"] == 10


def test_upgrade_in_force_covers_appointments_and_the_old_plan_does_not(ctx):
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    for index in range(10):
        first = ctx.pay(first, index)
    new = ctx.subscribe(ctx.premium, date(2026, 11, 10), date(2027, 11, 9), upgrade=True).json()
    # Before the upgrade starts: appointments still run on the old plan.
    booked = ctx.client.post(
        "/appointments",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": "2026-10-26T08:00"},
    )
    assert booked.status_code == 201, booked.text
    assert booked.json()["modality_id"] == ctx.basic["id"]
    # Once it starts, the old plan covers nothing and the new one must be paid.
    clock.travel_to(datetime(2026, 11, 16, 10, 0))
    refused = ctx.client.post(
        "/appointments",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": "2026-11-23T08:00"},
    )
    assert refused.status_code == 409 and "pending" in refused.json()["detail"]
    ctx.pay(ctx.get(new["id"]), 0, date(2026, 11, 10))
    booked = ctx.client.post(
        "/appointments",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": "2026-11-23T08:00"},
    )
    assert booked.status_code == 201, booked.text
    assert booked.json()["modality_id"] == ctx.premium["id"]
    assert booked.json()["discount_percent"] == 40


def test_edits_on_either_side_of_an_upgrade_keep_them_apart(ctx):
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    new = ctx.subscribe(ctx.premium, date(2026, 11, 10), date(2027, 11, 9), upgrade=True).json()
    # The old plan cannot run past the new one's start; the new one cannot start before the old ends.
    old_longer = ctx.client.put(
        f"/subscriptions/{first['id']}", headers=ctx.admin,
        json={"customer_id": ctx.holder, "modality_id": ctx.basic["id"], "joined_on": "2026-01-10", "expires_on": "2026-12-31"},
    )
    assert old_longer.status_code == 409 and "replaced by an upgrade" in old_longer.json()["detail"]
    new_earlier = ctx.client.put(
        f"/subscriptions/{new['id']}", headers=ctx.admin,
        json={"customer_id": ctx.holder, "modality_id": ctx.premium["id"], "joined_on": "2026-11-01", "expires_on": "2027-11-09"},
    )
    assert new_earlier.status_code == 409 and "starts after the one it replaced" in new_earlier.json()["detail"]
    fine = ctx.client.put(
        f"/subscriptions/{new['id']}", headers=ctx.admin,
        json={"customer_id": ctx.holder, "modality_id": ctx.premium["id"], "joined_on": "2026-11-10", "expires_on": "2027-06-30"},
    )
    assert fine.status_code == 200, fine.text
    # A third plan upgrades the one it overlaps — the scheduled upgrade, in
    # force on 01/12 — never stretching the first one (ended on 09/11) over it.
    third = ctx.subscribe(ctx.basic, date(2026, 12, 1), date(2027, 11, 30), upgrade=True)
    assert third.status_code == 201, third.text
    assert third.json()["upgraded_from_id"] == new["id"]
    assert ctx.get(new["id"])["expires_on"] == "2026-11-30"
    assert ctx.get(new["id"])["upgraded_to_id"] == third.json()["id"]
    assert ctx.get(first["id"])["expires_on"] == "2026-11-09"  # untouched: the plans never overlap


def test_clients_and_sellers_upgrade_too_and_everyone_is_told(ctx):
    region = ctx.client.post(
        "/regions", headers=ctx.admin,
        json={"name": "SP", "commission_bp": 1000, "areas": [{"state": "SP"}], "plans": [{"modality_id": ctx.premium["id"], "discount_percent": 5}]},
    ).json()
    seller = ctx.client.post("/sellers", headers=ctx.admin, json={"name": "Ana Seller", "region_id": region["id"]}).json()
    seller_headers = auth_as(ctx.client, "seller", seller_id=seller["id"])
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    first = ctx.subscribe(ctx.basic, date(2026, 1, 10), date(2027, 1, 9)).json()
    ctx.outbox.sent.clear()
    # The seller upgrades the customer to a plan of its region: a sale like any other.
    by_seller = ctx.subscribe(ctx.premium, date(2026, 10, 22), date(2027, 10, 21), headers=seller_headers, upgrade=True)
    assert by_seller.status_code == 201, by_seller.text
    assert by_seller.json()["seller_id"] == seller["id"] and by_seller.json()["monthly_amount_cents"] == 18905
    assert by_seller.json()["status"] == "pendente"
    assert ctx.get(first["id"])["status"] == "migrada"  # ended yesterday
    mine = ctx.client.get("/notifications", headers=client_headers).json()
    assert mine[0]["event"] == "subscription.upgraded"
    assert mine[0]["title"] == "Plan upgraded: Plan Foo → Plan Premium"
    assert "ends on 21/10/2026" in mine[0]["body"] and "starts on 22/10/2026" in mine[0]["body"]
    staff = ctx.client.get("/notifications", headers=ctx.admin).json()
    assert any(item["event"] == "subscription.upgraded" and "upgraded Plan Foo → Plan Premium" in item["title"] for item in staff)
    # The client upgrades itself (no seller): the plan in force is the pending premium one.
    by_client = ctx.subscribe(ctx.basic, date(2026, 10, 23), date(2027, 10, 22), headers=client_headers, upgrade=True)
    assert by_client.status_code == 201, by_client.text
    assert by_client.json()["upgraded_from_id"] == by_seller.json()["id"] and by_client.json()["seller_id"] is None
    assert ctx.get(by_seller.json()["id"])["installments_total"] == 1  # one day of premium: one installment kept
    assert ctx.client.get("/subscriptions", headers=client_headers).json().__len__() == 3

"""API tests for plan subscriptions, monthly installments and the plan
requirements enforced when scheduling appointments."""

from datetime import date, datetime, timedelta

import pytest

from app.billing import add_months
from conftest import activate_plan, auth, ensure_plan
from app import clock

TODAY = clock.today()
MONDAY_8 = datetime(2026, 10, 26, 8, 0)  # inside the default week of a new professional
WIDE_EXPIRY = max(TODAY, date(2026, 12, 31)) + timedelta(days=400)


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers)
    c.professional = client.post(
        "/professionals",
        headers=headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000},
    ).json()["id"]

    def customer(name="Foo Holder"):
        created = client.post("/customers", headers=headers, json={"name": name})
        assert created.status_code == 201
        return created.json()["id"]

    def subscribe(customer_id, joined_on, expires_on, plan=None):
        return client.post(
            "/subscriptions",
            headers=headers,
            json={
                "customer_id": customer_id,
                "modality_id": (plan or c.plan)["id"],
                "joined_on": joined_on.isoformat(),
                "expires_on": expires_on.isoformat(),
            },
        )

    def pay(subscription, installment, paid_on=None, paid_cents=None):
        body = {"paid_on": paid_on.isoformat() if paid_on else None}
        if paid_cents is not None:
            body["paid_cents"] = paid_cents
        return client.put(
            f"/subscriptions/{subscription['id']}/installments/{installment['id']}", headers=headers, json=body
        )

    def book(customer_id, marked_at=MONDAY_8, **overrides):
        payload = {"customer_id": customer_id, "professional_id": c.professional, "marked_at": marked_at.isoformat()}
        payload.update(overrides)
        return client.post("/appointments", headers=headers, json=payload)

    c.customer = customer
    c.subscribe = subscribe
    c.pay = pay
    c.book = book
    return c


# --- subscriptions and installments ----------------------------------------------


def test_create_subscription_generates_monthly_installments(ctx):
    holder = ctx.customer()
    response = ctx.subscribe(holder, date(2026, 1, 10), date(2027, 1, 9))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pendente"
    assert body["installments_total"] == 12
    assert body["installments_paid"] == 0
    assert body["monthly_amount_cents"] == 9900
    first = body["installments"][0]
    assert (first["sequence"], first["period_start"], first["period_end"], first["due_on"]) == (
        1, "2026-01-10", "2026-02-09", "2026-01-10",
    )  # fmt: skip
    assert first["amount_cents"] == 9900
    assert body["installments"][-1]["period_end"] == "2027-01-09"
    assert body["next_due_on"] == "2026-01-10"
    assert body["usable_from"] == "2026-01-10"  # grace_days 0 in the test plan


def test_expiration_is_required_and_after_join(ctx):
    holder = ctx.customer()
    missing = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": ctx.plan["id"], "joined_on": "2026-01-10"},
    )
    assert missing.status_code == 422
    assert ctx.subscribe(holder, date(2026, 1, 10), date(2026, 1, 10)).status_code == 422
    assert ctx.subscribe(holder, date(2026, 1, 10), date(2025, 12, 31)).status_code == 422


def test_status_is_derived_not_accepted_as_input(ctx):
    holder = ctx.customer()
    response = ctx.client.post(
        "/subscriptions",
        headers=ctx.headers,
        json={
            "customer_id": holder,
            "modality_id": ctx.plan["id"],
            "joined_on": (TODAY - timedelta(days=10)).isoformat(),
            "expires_on": WIDE_EXPIRY.isoformat(),
            "status": "ativa",  # ignored: nothing was paid
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pendente"


def test_one_current_subscription_per_customer(ctx):
    holder = ctx.customer()
    assert ctx.subscribe(holder, date(2026, 1, 10), date(2027, 1, 9)).status_code == 201
    second = ctx.subscribe(holder, date(2026, 6, 1), date(2027, 5, 31))
    assert second.status_code == 409
    assert "already has a subscription" in second.json()["detail"]


def test_paying_first_installment_activates_the_plan(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    paid = ctx.pay(sub, sub["installments"][0], paid_on=TODAY)
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["status"] == "ativa"
    assert body["installments_paid"] == 1
    assert body["installments"][0]["status"] == "paid"
    assert body["installments"][0]["paid_cents"] == 9900  # defaults to the amount due
    assert body["next_due_on"] == body["installments"][1]["due_on"]


def test_payment_amount_and_undo(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    paid = ctx.pay(sub, sub["installments"][0], paid_on=TODAY, paid_cents=9000).json()
    assert paid["installments"][0]["paid_cents"] == 9000
    undone = ctx.pay(sub, sub["installments"][0], paid_on=None)
    assert undone.status_code == 200
    assert undone.json()["status"] == "pendente"
    assert undone.json()["installments"][0]["paid_on"] is None


def test_payment_validation(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    future = ctx.pay(sub, sub["installments"][0], paid_on=TODAY + timedelta(days=1))
    assert future.status_code == 422
    other_holder = ctx.customer("Bar Holder")
    other = ctx.subscribe(other_holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    cross = ctx.client.put(
        f"/subscriptions/{other['id']}/installments/{sub['installments'][0]['id']}",
        headers=ctx.headers,
        json={"paid_on": TODAY.isoformat()},
    )
    assert cross.status_code == 404


def test_unpaid_past_due_installment_makes_the_plan_overdue(ctx):
    holder = ctx.customer()
    joined = add_months(TODAY, -3)
    sub = ctx.subscribe(holder, joined, WIDE_EXPIRY).json()
    sub = ctx.pay(sub, sub["installments"][0], paid_on=joined).json()
    assert sub["status"] == "inadimplente"
    assert sub["installments_overdue"] >= 1
    assert sub["installments"][1]["status"] == "overdue"
    for item in sub["installments"][1:]:
        if date.fromisoformat(item["due_on"]) <= TODAY:
            sub = ctx.pay(sub, item, paid_on=TODAY).json()
    assert sub["status"] == "ativa"
    assert sub["installments_overdue"] == 0


def test_expired_subscription_status(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, date(2024, 1, 10), date(2025, 1, 9)).json()
    for item in sub["installments"]:
        sub = ctx.pay(sub, item, paid_on=item["due_on"] and date.fromisoformat(item["due_on"])).json()
    assert sub["status"] == "vencida"


def test_cancel_and_reactivate(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    sub = ctx.pay(sub, sub["installments"][0], paid_on=TODAY).json()
    cancelled = ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers)
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelada"
    assert body["cancelled_on"] == TODAY.isoformat()
    # Future unpaid installments are dropped; the paid one stays.
    assert body["installments_total"] == 1
    assert body["installments_paid"] == 1
    assert ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers).status_code == 409
    # A new subscription is allowed once the old one is cancelled.
    fresh = ctx.subscribe(holder, TODAY, WIDE_EXPIRY)
    assert fresh.status_code == 201
    # ...and the cancelled one cannot come back while another is current.
    assert ctx.client.post(f"/subscriptions/{sub['id']}/reactivate", headers=ctx.headers).status_code == 409
    ctx.client.delete(f"/subscriptions/{fresh.json()['id']}", headers=ctx.headers)
    back = ctx.client.post(f"/subscriptions/{sub['id']}/reactivate", headers=ctx.headers)
    assert back.status_code == 200
    assert back.json()["status"] == "ativa"
    assert back.json()["installments_total"] > 1  # installments regenerated


def test_cancelled_subscription_cannot_be_edited(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY).json()
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers)
    response = ctx.client.put(
        f"/subscriptions/{sub['id']}",
        headers=ctx.headers,
        json={
            "customer_id": holder,
            "modality_id": ctx.plan["id"],
            "joined_on": (TODAY - timedelta(days=5)).isoformat(),
            "expires_on": WIDE_EXPIRY.isoformat(),
        },
    )
    assert response.status_code == 409


def test_editing_period_resyncs_unpaid_installments_and_keeps_paid_ones(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, date(2026, 1, 10), date(2027, 1, 9)).json()
    sub = ctx.pay(sub, sub["installments"][0], paid_on=date(2026, 1, 10)).json()
    shorter = ctx.client.put(
        f"/subscriptions/{sub['id']}",
        headers=ctx.headers,
        json={
            "customer_id": holder,
            "modality_id": ctx.plan["id"],
            "joined_on": "2026-01-10",
            "expires_on": "2026-06-30",
        },
    )
    assert shorter.status_code == 200, shorter.text
    body = shorter.json()
    assert body["installments_total"] == 6
    assert body["installments"][0]["status"] == "paid"
    assert body["installments"][-1]["period_end"] == "2026-06-30"
    too_short = ctx.client.put(
        f"/subscriptions/{sub['id']}",
        headers=ctx.headers,
        json={
            "customer_id": holder,
            "modality_id": ctx.plan["id"],
            "joined_on": "2025-12-01",
            "expires_on": "2025-12-31",  # before the paid installment
        },
    )
    assert too_short.status_code == 409


def test_changing_plan_reprices_unpaid_installments(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, date(2026, 1, 10), date(2027, 1, 9)).json()
    sub = ctx.pay(sub, sub["installments"][0], paid_on=date(2026, 1, 10)).json()
    premium = ensure_plan(ctx.client, ctx.headers, name="Plan Premium", plan_value_cents=19900)
    response = ctx.client.put(
        f"/subscriptions/{sub['id']}",
        headers=ctx.headers,
        json={"customer_id": holder, "modality_id": premium["id"], "joined_on": "2026-01-10", "expires_on": "2027-01-09"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["monthly_amount_cents"] == 19900
    assert body["installments"][0]["amount_cents"] == 9900  # paid: untouched
    assert body["installments"][1]["amount_cents"] == 19900


def test_dependents_above_the_limit_raise_unpaid_installments(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, date(2026, 1, 10), date(2027, 1, 9)).json()
    sub = ctx.pay(sub, sub["installments"][0], paid_on=date(2026, 1, 10)).json()
    for name in ("A", "B", "C"):
        created = ctx.client.post(
            f"/customers/{holder}/dependents",
            headers=ctx.headers,
            json={"name": f"Child {name}", "birth_date": "2018-02-20"},
        )
        assert created.status_code == 201
    body = ctx.client.get(f"/subscriptions/{sub['id']}", headers=ctx.headers).json()
    assert body["dependents_count"] == 3
    assert body["monthly_amount_cents"] == 9900 + 1500  # limit 2, one extra
    assert body["installments"][0]["amount_cents"] == 9900
    assert body["installments"][1]["amount_cents"] == 11400
    deps = ctx.client.get(f"/customers/{holder}/dependents", headers=ctx.headers).json()
    ctx.client.delete(f"/customers/{holder}/dependents/{deps[0]['id']}", headers=ctx.headers)
    body = ctx.client.get(f"/subscriptions/{sub['id']}", headers=ctx.headers).json()
    assert body["installments"][1]["amount_cents"] == 9900


def test_customer_subscription_lookup(ctx):
    holder = ctx.customer()
    assert ctx.client.get(f"/subscriptions/customer/{holder}", headers=ctx.headers).json() is None
    sub = ctx.subscribe(holder, TODAY, WIDE_EXPIRY).json()
    found = ctx.client.get(f"/subscriptions/customer/{holder}", headers=ctx.headers).json()
    assert found["id"] == sub["id"]
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers)
    assert ctx.client.get(f"/subscriptions/customer/{holder}", headers=ctx.headers).json() is None
    assert ctx.client.get("/subscriptions/customer/9999", headers=ctx.headers).status_code == 404


# --- plan requirements when scheduling ------------------------------------------------


def test_no_subscription_blocks_scheduling(ctx):
    holder = ctx.customer()
    response = ctx.book(holder)
    assert response.status_code == 409
    assert response.json()["detail"] == "Customer has no plan subscription"


def test_pending_subscription_blocks_scheduling(ctx):
    holder = ctx.customer()
    ctx.subscribe(holder, TODAY - timedelta(days=5), WIDE_EXPIRY)
    response = ctx.book(holder)
    assert response.status_code == 409
    assert "pending" in response.json()["detail"]


def test_active_subscription_allows_scheduling(ctx):
    holder = ctx.customer()
    activate_plan(ctx.client, ctx.headers, holder)
    assert ctx.book(holder).status_code == 201


def test_overdue_subscription_blocks_scheduling(ctx):
    holder = ctx.customer()
    joined = add_months(TODAY, -3)
    sub = ctx.subscribe(holder, joined, WIDE_EXPIRY).json()
    ctx.pay(sub, sub["installments"][0], paid_on=joined)
    response = ctx.book(holder)
    assert response.status_code == 409
    assert "overdue payment" in response.json()["detail"]


def test_cancelled_subscription_blocks_scheduling(ctx):
    holder = ctx.customer()
    sub = activate_plan(ctx.client, ctx.headers, holder)
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers)
    response = ctx.book(holder)
    assert response.status_code == 409
    assert "cancelled" in response.json()["detail"]


def test_expired_subscription_blocks_scheduling(ctx):
    holder = ctx.customer()
    sub = ctx.subscribe(holder, date(2024, 1, 10), date(2025, 1, 9)).json()
    for item in sub["installments"]:
        ctx.pay(sub, item, paid_on=date.fromisoformat(item["due_on"]))
    response = ctx.book(holder)
    assert response.status_code == 409
    assert "expired on 2025-01-09" in response.json()["detail"]


def test_appointment_after_expiration_is_blocked(ctx):
    holder = ctx.customer()
    sub = activate_plan(ctx.client, ctx.headers, holder, expires_on=date(2026, 10, 26))  # next Monday
    assert sub["status"] == "ativa"
    assert ctx.book(holder, marked_at=MONDAY_8).status_code == 201  # expiration day is covered
    later = ctx.book(holder, marked_at=MONDAY_8 + timedelta(days=1))
    assert later.status_code == 409
    assert "expires on 2026-10-26, before the appointment" in later.json()["detail"]


def test_waiting_period_blocks_early_appointments(ctx):
    holder = ctx.customer()
    plan = ensure_plan(ctx.client, ctx.headers, name="Plan Waiting", grace_days=30)
    joined = min(TODAY, date(2026, 10, 10))
    activate_plan(ctx.client, ctx.headers, holder, plan=plan, joined_on=joined, expires_on=WIDE_EXPIRY)
    early = ctx.book(holder, marked_at=datetime.combine(joined + timedelta(days=15), datetime.min.time()).replace(hour=8))
    assert early.status_code == 409
    assert "waiting period ends on" in early.json()["detail"]


def test_appointment_before_join_date_is_blocked(ctx):
    holder = ctx.customer()
    sub = activate_plan(ctx.client, ctx.headers, holder, joined_on=date(2026, 10, 27), expires_on=WIDE_EXPIRY, pay=False)
    # Paying ahead of the due date is allowed, so the plan is active before it starts.
    sub = ctx.pay(sub, sub["installments"][0], paid_on=TODAY).json()
    assert sub["status"] == "ativa"
    response = ctx.book(holder, marked_at=MONDAY_8)
    assert response.status_code == 409
    assert response.json()["detail"] == "Subscription starts on 2026-10-27"


def test_dependent_is_covered_by_the_holder_plan(ctx):
    holder = ctx.customer()
    child = ctx.client.post(
        f"/customers/{holder}/dependents", headers=ctx.headers, json={"name": "Child", "birth_date": "2018-02-20"}
    ).json()
    assert ctx.book(holder, dependent_id=child["id"]).status_code == 409  # no plan yet
    activate_plan(ctx.client, ctx.headers, holder)
    assert ctx.book(holder, dependent_id=child["id"]).status_code == 201


def test_cancelled_appointment_does_not_require_a_plan(ctx):
    holder = ctx.customer()
    assert ctx.book(holder, status="cancelada").status_code == 201


def test_plan_is_checked_before_the_slot(ctx):
    # Without a plan the answer is about the plan, even if the slot is also bad.
    holder = ctx.customer()
    response = ctx.book(holder, marked_at=datetime(2026, 10, 31, 8, 0))  # Saturday: no hours
    assert response.status_code == 409
    assert "plan" in response.json()["detail"]


def test_editing_existing_appointment_does_not_recheck_unless_rebooked(ctx):
    holder = ctx.customer()
    sub = activate_plan(ctx.client, ctx.headers, holder)
    item = ctx.book(holder).json()
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.headers)
    base = {"customer_id": holder, "professional_id": ctx.professional, "marked_at": MONDAY_8.isoformat()}
    # Notes are record keeping: allowed. (Completion is the professional's
    # signature, which does not re-check the plan either.)
    assert ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**base, "notes": "x"}).status_code == 200
    # Moving to another day is a new booking: blocked.
    moved = ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.headers,
        json={**base, "marked_at": (MONDAY_8 + timedelta(days=1)).isoformat()},
    )
    assert moved.status_code == 409
    # Cancelling and re-activating is a new booking too.
    assert ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**base, "status": "cancelada"}).status_code == 200
    assert ctx.client.put(f"/appointments/{item['id']}", headers=ctx.headers, json={**base, "status": "agendada"}).status_code == 409


def test_editing_to_another_customer_checks_that_customer(ctx):
    holder = ctx.customer()
    other = ctx.customer("Bar Holder")
    activate_plan(ctx.client, ctx.headers, holder)
    item = ctx.book(holder).json()
    response = ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.headers,
        json={"customer_id": other, "professional_id": ctx.professional, "marked_at": MONDAY_8.isoformat()},
    )
    assert response.status_code == 409
    assert "no plan subscription" in response.json()["detail"]


# --- catalog: plan editing -----------------------------------------------------------


def test_plan_can_be_fetched_and_edited(ctx):
    fetched = ctx.client.get(f"/modalities/{ctx.plan['id']}", headers=ctx.headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Plan Foo"
    updated = ctx.client.put(
        f"/modalities/{ctx.plan['id']}",
        headers=ctx.headers,
        json={**fetched.json(), "plan_value_cents": 12900, "grace_days": 15},
    )
    assert updated.status_code == 200
    assert updated.json()["plan_value_cents"] == 12900
    assert ctx.client.get(f"/modalities/{ctx.plan['id']}", headers=ctx.headers).json()["grace_days"] == 15
    assert ctx.client.get("/modalities/9999", headers=ctx.headers).status_code == 404


def test_specialty_can_be_fetched(ctx):
    assert ctx.client.get("/specialties/1", headers=ctx.headers).json()["name"] == "Clínico geral"
    assert ctx.client.get("/specialties/9999", headers=ctx.headers).status_code == 404

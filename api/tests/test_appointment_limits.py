"""API tests for the plan's appointment limit: appointments over the
allowance of the period are accepted but pay the full price, and the
usage is reported on quotes, subscriptions and reports."""

from datetime import date, datetime, timedelta

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock

# The subscription used here joins on 2026-09-10, so the monthly periods are
# the 10th to the 9th: October's is 2026-10-10 .. 2026-11-09.
JOINED_ON = date(2026, 9, 10)
EXPIRES_ON = date(2027, 9, 9)
OCT = [datetime(2026, 10, 26, 8, 0), datetime(2026, 10, 26, 8, 30), datetime(2026, 10, 26, 9, 0), datetime(2026, 10, 26, 9, 30)]
NOV = datetime(2026, 11, 16, 8, 0)  # Monday of the next period
# Test catalog: clinic fee 2000 + specialty 8000 + professional 5000; 20% off -> 12000.
GROSS, DISCOUNTED = 15000, 12000


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers, discount_percent=20, appointment_limit=2)
    c.professional = client.post(
        "/professionals", headers=headers, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"}).json()["id"]
    c.subscription = activate_plan(client, headers, c.holder, plan=c.plan, joined_on=JOINED_ON, expires_on=EXPIRES_ON)

    def book(when, customer_id=None, **overrides):
        payload = {"customer_id": customer_id or c.holder, "professional_id": c.professional, "marked_at": when.isoformat()}
        payload.update(overrides)
        response = client.post("/appointments", headers=headers, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    def edit(item, **overrides):
        payload = {
            "customer_id": item["customer_id"],
            "dependent_id": item["dependent_id"],
            "professional_id": item["professional_id"],
            "marked_at": item["marked_at"],
            "status": item["status"],
            "notes": item["notes"],
        }
        payload.update(overrides)
        if isinstance(payload["marked_at"], datetime):
            payload["marked_at"] = payload["marked_at"].isoformat()
        return client.put(f"/appointments/{item['id']}", headers=headers, json=payload)

    def quote(when, customer_id=None, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        response = client.get(
            f"/appointments/quote?customer_id={customer_id or c.holder}&professional_id={c.professional}&on={when:%Y-%m-%d}&{query}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def subscription():
        return client.get(f"/subscriptions/{c.subscription['id']}", headers=headers).json()

    c.book, c.edit, c.quote, c.subscription_out = book, edit, quote, subscription
    return c


# --- plan record -------------------------------------------------------------------


def test_plan_carries_the_limit_and_its_period(ctx):
    assert ctx.plan["appointment_limit"] == 2
    assert ctx.plan["appointment_limit_period"] == "month"
    created = ctx.client.post(
        "/modalities",
        headers=ctx.headers,
        json={"name": "Yearly", "appointment_limit": 10, "appointment_limit_period": "subscription"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["appointment_limit_period"] == "subscription"
    unlimited = ctx.client.post("/modalities", headers=ctx.headers, json={"name": "Open"}).json()
    assert (unlimited["appointment_limit"], unlimited["appointment_limit_period"]) == (0, "month")


def test_plan_limit_validation(ctx):
    assert ctx.client.post("/modalities", headers=ctx.headers, json={"name": "Bad", "appointment_limit": -1}).status_code == 422
    assert (
        ctx.client.post("/modalities", headers=ctx.headers, json={"name": "Bad", "appointment_limit_period": "week"}).status_code == 422
    )


# --- booking -------------------------------------------------------------------------


def test_appointments_over_the_limit_pay_the_full_price(ctx):
    first = ctx.book(OCT[0])
    second = ctx.book(OCT[1])
    third = ctx.book(OCT[2])
    for item in (first, second):
        assert item["over_limit"] is False
        assert item["discount_percent"] == 20
        assert item["amount_cents"] == DISCOUNTED
        assert item["modality_id"] == ctx.plan["id"]
    # Accepted, but no discount.
    assert third["over_limit"] is True
    assert third["discount_percent"] == 0
    assert third["discount_cents"] == 0
    assert third["amount_cents"] == GROSS
    assert third["modality_id"] == ctx.plan["id"]  # the plan is still recorded
    assert (third["plan_limit"], third["plan_limit_period"]) == (2, "month")
    # The snapshot of the limit stays with the appointment.
    listed = ctx.client.get(f"/appointments/{third['id']}", headers=ctx.headers).json()
    assert listed["over_limit"] is True


def test_the_allowance_renews_each_billing_period(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    assert ctx.book(OCT[2])["over_limit"] is True
    # Next monthly period (from 2026-11-10): a fresh allowance.
    assert ctx.book(NOV)["over_limit"] is False
    # The periods follow the join day, not the calendar month: 2026-11-09 is still October's.
    assert ctx.book(datetime(2026, 11, 9, 8, 0))["over_limit"] is True


def test_unlimited_plan_never_reaches_the_limit(ctx):
    updated = ctx.client.put("/modalities/%d" % ctx.plan["id"], headers=ctx.headers, json={**ctx.plan, "appointment_limit": 0})
    assert updated.status_code == 200, updated.text
    for when in OCT:
        item = ctx.book(when)
        assert item["over_limit"] is False
        assert item["amount_cents"] == DISCOUNTED
        assert item["plan_limit"] == 0


def test_limit_per_subscription_period(ctx):
    updated = ctx.client.put(
        "/modalities/%d" % ctx.plan["id"], headers=ctx.headers, json={**ctx.plan, "appointment_limit_period": "subscription"}
    )
    assert updated.status_code == 200, updated.text
    ctx.book(OCT[0])
    ctx.book(NOV)  # another month: still the same allowance
    late = ctx.book(datetime(2026, 12, 14, 8, 0))
    assert late["over_limit"] is True
    assert late["plan_limit_period"] == "subscription"


def test_cancelled_appointments_give_their_place_back(ctx):
    first = ctx.book(OCT[0])
    ctx.book(OCT[1])
    cancelled = ctx.edit(first, status="cancelada")
    assert cancelled.status_code == 200, cancelled.text
    # Cancelling does not re-price the cancelled one...
    assert cancelled.json()["amount_cents"] == DISCOUNTED
    # ...but frees a place for the next booking.
    assert ctx.book(OCT[2])["over_limit"] is False
    assert ctx.book(OCT[3])["over_limit"] is True


def test_over_limit_appointments_do_not_take_a_place(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    assert full["over_limit"] is True
    # Cancelling a covered one frees a place even though a full-price one exists.
    assert ctx.edit(ctx.client.get("/appointments", headers=ctx.headers).json()[-1], status="cancelada").status_code == 200
    assert ctx.book(OCT[3])["over_limit"] is False


def test_completed_appointments_count(ctx):
    first = ctx.book(OCT[0])
    sign(ctx.client, first["id"])
    ctx.book(OCT[1])
    assert ctx.book(OCT[2])["over_limit"] is True


def test_dependents_share_the_holder_allowance(ctx):
    child = ctx.client.post(
        f"/customers/{ctx.holder}/dependents", headers=ctx.headers, json={"name": "Ipsum Child", "birth_date": "2018-02-20"}
    ).json()["id"]
    ctx.book(OCT[0], dependent_id=child)
    ctx.book(OCT[1])
    third = ctx.book(OCT[2], dependent_id=child)
    assert third["over_limit"] is True
    assert third["amount_cents"] == GROSS


def test_other_customers_have_their_own_allowance(ctx):
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.headers, other, plan=ctx.plan, joined_on=JOINED_ON, expires_on=EXPIRES_ON)
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    assert ctx.book(OCT[2], customer_id=other)["over_limit"] is False


def test_limit_is_a_snapshot(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    assert full["over_limit"] is True
    # Raising the limit afterwards does not re-price what was booked...
    ctx.client.put("/modalities/%d" % ctx.plan["id"], headers=ctx.headers, json={**ctx.plan, "appointment_limit": 5})
    edited = ctx.edit(full, notes="follow-up")
    assert edited.status_code == 200
    assert edited.json()["over_limit"] is True
    assert edited.json()["amount_cents"] == GROSS
    # ...but a new booking uses the new allowance.
    assert ctx.book(OCT[3])["over_limit"] is False


# --- editing re-evaluates the allowance -------------------------------------------------


def test_moving_to_another_period_re_evaluates_the_allowance(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    assert full["over_limit"] is True
    moved = ctx.edit(full, marked_at=NOV)
    assert moved.status_code == 200, moved.text
    assert moved.json()["over_limit"] is False
    assert moved.json()["discount_percent"] == 20
    assert moved.json()["amount_cents"] == DISCOUNTED
    # And back into the full period: over the limit again.
    back = ctx.edit(moved.json(), marked_at=OCT[2])
    assert back.json()["over_limit"] is True
    assert back.json()["amount_cents"] == GROSS


def test_editing_keeps_the_price_list_snapshot_when_re_evaluating(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    ctx.client.put(
        f"/professionals/{ctx.professional}",
        headers=ctx.headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 9000},
    )
    moved = ctx.edit(full, marked_at=NOV).json()
    assert moved["professional_price_cents"] == 5000  # fee snapshot kept
    assert moved["gross_cents"] == GROSS
    assert moved["amount_cents"] == DISCOUNTED


def test_moving_within_the_period_does_not_count_itself(ctx):
    first = ctx.book(OCT[0])
    ctx.book(OCT[1])
    moved = ctx.edit(first, marked_at=OCT[2])
    assert moved.status_code == 200, moved.text
    assert moved.json()["over_limit"] is False
    assert moved.json()["amount_cents"] == DISCOUNTED


def test_reactivating_a_cancelled_appointment_re_evaluates(ctx):
    first = ctx.book(OCT[0])
    cancelled = ctx.edit(first, status="cancelada").json()
    ctx.book(OCT[1])
    ctx.book(OCT[2])
    back = ctx.edit(cancelled, status="agendada")
    assert back.status_code == 200, back.text
    assert back.json()["over_limit"] is True
    assert back.json()["amount_cents"] == GROSS


def test_changing_customer_uses_that_customer_allowance(ctx):
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(ctx.client, ctx.headers, other, plan=ctx.plan, joined_on=JOINED_ON, expires_on=EXPIRES_ON)
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    moved = ctx.edit(full, customer_id=other)
    assert moved.status_code == 200, moved.text
    assert moved.json()["over_limit"] is False
    assert moved.json()["amount_cents"] == DISCOUNTED


def test_editing_notes_does_not_re_evaluate(ctx):
    first = ctx.book(OCT[0])
    ctx.book(OCT[1])
    ctx.book(OCT[2])
    # Cancel the first: a place is free, but the third keeps its snapshot.
    ctx.edit(first, status="cancelada")
    third = ctx.client.get("/appointments", headers=ctx.headers).json()[0]
    assert third["over_limit"] is True
    edited = ctx.edit(third, notes="x").json()
    assert edited["over_limit"] is True
    assert edited["amount_cents"] == GROSS


# --- quote -------------------------------------------------------------------------


def test_quote_reports_the_allowance_of_the_period(ctx):
    before = ctx.quote(OCT[0])
    assert before["over_limit"] is False
    assert (before["plan_limit"], before["plan_limit_period"]) == (2, "month")
    assert (before["plan_used"], before["plan_remaining"]) == (0, 2)
    assert (before["plan_window_start"], before["plan_window_end"]) == ("2026-10-10", "2026-11-09")
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    after = ctx.quote(OCT[2])
    assert after["over_limit"] is True
    assert (after["plan_used"], after["plan_remaining"]) == (2, 0)
    assert after["discount_percent"] == 0
    assert after["amount_cents"] == GROSS
    # Another period: covered again.
    assert ctx.quote(NOV)["over_limit"] is False


def test_quote_excludes_the_appointment_being_edited(ctx):
    first = ctx.book(OCT[0])
    ctx.book(OCT[1])
    assert ctx.quote(OCT[2])["over_limit"] is True
    editing = ctx.quote(OCT[2], appointment_id=first["id"])
    assert editing["over_limit"] is False
    assert editing["plan_used"] == 1


def test_quote_without_plan_has_no_allowance(ctx):
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    body = ctx.quote(OCT[0], customer_id=other)
    assert body["modality_id"] is None
    assert body["over_limit"] is False
    assert body["plan_limit"] == 0
    assert body["plan_remaining"] is None


def test_quote_of_unlimited_plan(ctx):
    ctx.client.put("/modalities/%d" % ctx.plan["id"], headers=ctx.headers, json={**ctx.plan, "appointment_limit": 0})
    ctx.book(OCT[0])
    body = ctx.quote(OCT[1])
    assert body["over_limit"] is False
    assert body["plan_limit"] == 0
    assert body["plan_used"] == 1
    assert body["plan_remaining"] is None


# --- subscription usage ------------------------------------------------------------


def test_subscription_reports_usage_per_installment(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    ctx.book(OCT[2])  # over the limit: not a covered one
    ctx.book(NOV)
    body = ctx.subscription_out()
    assert body["appointment_limit"] == 2
    assert body["appointment_limit_period"] == "month"
    by_period = {item["period_start"]: item["appointments_used"] for item in body["installments"]}
    assert by_period["2026-10-10"] == 2
    assert by_period["2026-11-10"] == 1
    assert by_period["2026-09-10"] == 0


def test_subscription_reports_usage_of_the_current_period(ctx):
    # Today on the test clock (2026-10-22) falls in the period 2026-10-10 .. 2026-11-09.
    body = ctx.subscription_out()
    assert (body["usage_window_start"], body["usage_window_end"]) == ("2026-10-10", "2026-11-09")
    assert (body["appointments_used"], body["appointments_remaining"]) == (0, 2)
    ctx.book(OCT[0])  # next Monday, same period
    body = ctx.subscription_out()
    assert body["appointments_used"] == 1
    assert body["appointments_remaining"] == 1


def test_client_sees_its_own_usage(ctx):
    ctx.book(OCT[0])
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    body = ctx.client.get(f"/subscriptions/customer/{ctx.holder}", headers=client_headers).json()
    assert body["appointment_limit"] == 2
    listed = ctx.client.get("/appointments", headers=client_headers).json()
    assert listed[0]["over_limit"] is False


# --- reports ---------------------------------------------------------------------------


def test_appointments_report_counts_over_limit_appointments(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    ctx.book(OCT[2])
    ctx.book(OCT[3], status="cancelada")  # cancelled: never counted as over limit money
    body = ctx.client.get(
        "/reports/appointments?granularity=week&start=2026-10-26&end=2026-11-01", headers=ctx.headers
    ).json()
    assert body["totals"]["over_limit"] == 1
    assert body["totals"]["over_limit_cents"] == GROSS
    assert body["totals"]["amount_cents"] == 2 * DISCOUNTED + GROSS
    assert body["totals"]["discount_cents"] == 2 * 3000
    (week,) = body["periods"]
    assert (week["over_limit"], week["over_limit_cents"]) == (1, GROSS)
    by_plan = body["by_plan"][0]
    assert by_plan["name"] == "Plan Foo"
    assert (by_plan["over_limit"], by_plan["over_limit_cents"]) == (1, GROSS)
    assert body["by_professional"][0]["over_limit"] == 1


def test_plans_report_shows_covered_and_over_limit_appointments(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    ctx.book(OCT[2])
    ctx.book(NOV)
    body = ctx.client.get("/reports/plans?granularity=month&start=2026-10-01&end=2026-11-30", headers=ctx.headers).json()
    (row,) = body["by_plan"]
    assert row["name"] == "Plan Foo"
    assert (row["appointment_limit"], row["appointment_limit_period"]) == (2, "month")
    assert row["appointments_covered"] == 3
    assert row["appointments_over_limit"] == 1
    assert row["over_limit_cents"] == GROSS
    assert body["totals"]["appointments_covered"] == 3
    assert body["totals"]["appointments_over_limit"] == 1
    october, november = body["periods"]
    assert (october["appointments_covered"], october["appointments_over_limit"]) == (2, 1)
    assert (november["appointments_covered"], november["appointments_over_limit"]) == (1, 0)


def test_finance_report_separates_over_limit_revenue(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    sign(ctx.client, full["id"])
    body = ctx.client.get("/reports/finance?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.headers).json()
    assert body["totals"]["appointments_completed"] == 1
    assert body["totals"]["appointments_over_limit"] == 1
    assert body["totals"]["appointments_over_limit_cents"] == GROSS
    assert body["totals"]["appointments_amount_cents"] == GROSS


def test_payout_statement_and_csv_flag_over_limit_appointments(ctx):
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    full = ctx.book(OCT[2])
    sign(ctx.client, full["id"])
    statement = ctx.client.get(
        f"/reports/payouts/{ctx.professional}?start=2026-10-26&end=2026-10-26", headers=ctx.headers
    ).json()
    flags = {line["appointment_id"]: line["over_limit"] for line in statement["lines"]}
    assert flags[full["id"]] is True
    assert sum(1 for value in flags.values() if value) == 1
    # The professional's fee is the same either way: the full price goes to the network.
    line = next(line for line in statement["lines"] if line["appointment_id"] == full["id"])
    assert line["payout_cents"] == 5000
    assert line["amount_cents"] == GROSS
    csv = ctx.client.get("/reports/payouts.csv?start=2026-10-26&end=2026-10-26", headers=ctx.headers).text
    header, *rows = csv.strip().splitlines()
    assert "over_limit" in header.split(";")
    assert len(rows) == 1 and ";yes;" in rows[0]


# --- notifications -------------------------------------------------------------------


def test_over_limit_booking_tells_the_customer(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    ctx.book(OCT[0])
    ctx.book(OCT[1])
    ctx.book(OCT[2])
    bodies = [item["body"] for item in ctx.client.get("/notifications", headers=client_headers).json()]
    assert any("full price" in body and "2 appointments per month" in body for body in bodies), bodies
    assert sum(1 for body in bodies if "20% plan discount" in body) == 2


# --- virtual card ----------------------------------------------------------------------


def test_virtual_card_shows_what_is_left(ctx):
    client_headers = auth_as(ctx.client, "client", customer_id=ctx.holder)
    card = ctx.client.get(f"/customers/{ctx.holder}/card", headers=client_headers).json()
    assert card["appointment_limit"] == 2
    assert card["appointments_remaining"] == 2

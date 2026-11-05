"""API tests for the seller role: its login (lost at once when deactivated),
its own area (sales to anyone inside its region, customers, region,
commissions, account), the change requests the customer confirms, what it
is kept out of, and the seller notifications."""

from datetime import date

import pytest

from conftest import auth, auth_as, ensure_plan

JOINED = date(2026, 10, 1)
EXPIRES = date(2027, 9, 30)


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.outbox = outbox
    c.plan = ensure_plan(client, admin)  # 99.00/month
    c.other_plan = ensure_plan(client, admin, name="Plan Bar", plan_value_cents=19900)
    c.region = client.post(
        "/regions",
        headers=admin,
        json={
            "name": "Interior SP",
            "commission_bp": 1000,
            "areas": [{"state": "SP"}],
            "plans": [{"modality_id": c.plan["id"], "discount_percent": 10}],
        },
    ).json()
    c.seller = client.post(
        "/sellers", headers=admin, json={"name": "Ana Seller", "region_id": c.region["id"], "email": "ana@example.com"}
    ).json()
    c.other_seller = client.post("/sellers", headers=admin, json={"name": "Bia Seller", "region_id": c.region["id"]}).json()
    c.headers = auth_as(client, "seller", seller_id=c.seller["id"])  # seller<id>@example.com
    outbox.sent.clear()

    def customer(name="Foo Holder", headers=None, city="Campinas", state="SP"):
        response = client.post("/customers", headers=headers or c.headers, json={"name": name, "city": city, "state": state})
        assert response.status_code == 201, response.text
        return response.json()

    def sell(customer_id, headers=None, plan=None, **extra):
        body = {
            "customer_id": customer_id,
            "modality_id": (plan or c.plan)["id"],
            "joined_on": JOINED.isoformat(),
            "expires_on": EXPIRES.isoformat(),
            **extra,
        }
        return client.post("/subscriptions", headers=headers or c.headers, json=body)

    def notifications(headers):
        return client.get("/notifications", headers=headers).json()

    c.customer = customer
    c.sell = sell
    c.notifications = notifications
    return c


# --- login and account -----------------------------------------------------------------


def test_seller_login_is_linked_to_its_seller_record(ctx):
    me = ctx.client.get("/auth/me", headers=ctx.headers).json()
    assert me["role"] == "seller"
    assert me["seller_id"] == ctx.seller["id"]
    assert me["seller_name"] == "Ana Seller"
    # The login must point at a seller, and only seller logins do.
    missing = ctx.client.post(
        "/users", headers=ctx.admin, json={"name": "X", "email": "x@example.com", "role": "seller", "password": "saude123"}
    )
    assert missing.status_code == 422
    wrong = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "X", "email": "x@example.com", "role": "attendant", "password": "saude123", "seller_id": ctx.seller["id"]},
    )
    assert wrong.status_code == 422
    unknown = ctx.client.post(
        "/users", headers=ctx.admin, json={"name": "X", "email": "x@example.com", "role": "seller", "password": "saude123", "seller_id": 999}
    )
    assert unknown.status_code == 404
    # Supervisors manage client logins only.
    supervisor = auth_as(ctx.client, "supervisor")
    refused = ctx.client.post(
        "/users",
        headers=supervisor,
        json={"name": "X", "email": "x@example.com", "role": "seller", "password": "saude123", "seller_id": ctx.seller["id"]},
    )
    assert refused.status_code == 403


def test_seller_manages_its_own_record_and_password(ctx):
    me = ctx.client.get("/sellers/me", headers=ctx.headers).json()
    assert me["name"] == "Ana Seller"
    assert me["region"]["name"] == "Interior SP"
    assert me["region"]["commission_label"] == "10%"
    assert me["region"]["plans"][0]["modality_name"] == "Plan Foo"
    assert me["customers_count"] == 0
    updated = ctx.client.put(
        "/sellers/me", headers=ctx.headers, json={"document": "123.456.789-00", "email": "ana.new@example.com", "phone": "(11) 9"}
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["email"], updated.json()["phone"]) == ("ana.new@example.com", "(11) 9")
    staff_view = ctx.client.get(f"/sellers/{ctx.seller['id']}", headers=ctx.admin).json()
    assert staff_view["document"] == "123.456.789-00"
    assert staff_view["name"] == "Ana Seller"  # name, region and status stay the staff's

    wrong = ctx.client.put("/auth/password", headers=ctx.headers, json={"current_password": "nope", "new_password": "another1"})
    assert wrong.status_code == 400
    same = ctx.client.put("/auth/password", headers=ctx.headers, json={"current_password": "saude123", "new_password": "saude123"})
    assert same.status_code == 422
    changed = ctx.client.put("/auth/password", headers=ctx.headers, json={"current_password": "saude123", "new_password": "another1"})
    assert changed.status_code == 204
    email = f"seller{ctx.seller['id']}@example.com"
    assert ctx.client.post("/auth/token", json={"email": email, "password": "saude123"}).status_code == 401
    assert ctx.client.post("/auth/token", json={"email": email, "password": "another1"}).status_code == 200


# --- what the seller is kept out of ----------------------------------------------------


def test_seller_has_no_access_outside_its_area(ctx):
    forbidden = [
        ("GET", "/appointments"),
        ("GET", "/regions"),
        ("GET", f"/regions/{ctx.region['id']}"),
        ("GET", "/sellers"),
        ("GET", f"/sellers/{ctx.seller['id']}"),
        ("GET", "/users"),
        ("GET", "/reports/plans"),
        ("GET", "/reports/finance"),
        ("GET", "/reports/payouts"),
        ("GET", "/payouts/position"),
        ("GET", "/tax-rules"),
        ("GET", "/queue"),
    ]
    for method, path in forbidden:
        response = ctx.client.request(method, path, headers=ctx.headers)
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
    # Writing the catalog is for managers; reading it is open (sellers quote plans).
    assert ctx.client.get("/modalities", headers=ctx.headers).status_code == 200
    assert ctx.client.post("/modalities", headers=ctx.headers, json={"name": "X"}).status_code == 403
    assert ctx.client.post("/regions", headers=ctx.headers, json={"name": "X", "areas": [{"state": "SP"}]}).status_code == 403
    # Staff-only actions on customers and sales.
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    assert ctx.client.delete(f"/customers/{holder['id']}", headers=ctx.headers).status_code == 403
    assert ctx.client.post(f"/customers/{holder['id']}/card/reset", headers=ctx.headers).status_code == 403
    first = sold["installments"][0]
    assert (
        ctx.client.put(
            f"/subscriptions/{sold['id']}/installments/{first['id']}", headers=ctx.headers, json={"paid_on": "2026-10-01"}
        ).status_code
        == 403
    )
    # Direct edits and cancellations are not the seller's: it asks, the customer confirms.
    assert ctx.client.post(f"/subscriptions/{sold['id']}/cancel", headers=ctx.headers).status_code == 403
    assert ctx.client.delete(f"/subscriptions/{sold['id']}", headers=ctx.headers).status_code == 403
    edit = ctx.client.put(
        f"/subscriptions/{sold['id']}",
        headers=ctx.headers,
        json={"customer_id": holder["id"], "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-06-30"},
    )
    assert edit.status_code == 403
    assert "confirms" in edit.json()["detail"]


# --- customers: anyone ---------------------------------------------------------------------


def test_seller_reaches_every_customer_record(ctx):
    mine = ctx.customer("Registered By Me")
    assert mine["seller_id"] == ctx.seller["id"]  # who registered it is remembered
    staff_customer = ctx.customer("Registered By Staff", headers=ctx.admin)
    assert staff_customer["seller_id"] is None

    listed = ctx.client.get("/customers", headers=ctx.headers).json()
    assert sorted(row["name"] for row in listed) == ["Registered By Me", "Registered By Staff"]
    assert ctx.client.get(f"/customers/{staff_customer['id']}", headers=ctx.headers).status_code == 200
    assert ctx.client.get(f"/customers/{staff_customer['id']}/dependents", headers=ctx.headers).status_code == 200
    edited = ctx.client.put(
        f"/customers/{staff_customer['id']}", headers=ctx.headers, json={"name": "Registered By Staff", "city": "Santos", "state": "SP"}
    )
    assert edited.status_code == 200 and edited.json()["seller_id"] is None  # editing does not take it over
    dependent = ctx.client.post(
        f"/customers/{staff_customer['id']}/dependents", headers=ctx.headers, json={"name": "Kid", "birth_date": "2018-02-20"}
    )
    assert dependent.status_code == 201, dependent.text
    assert ctx.client.delete(f"/customers/{staff_customer['id']}/dependents/{dependent.json()['id']}", headers=ctx.headers).status_code == 204
    # Selling to a customer registered by the staff is a sale like any other.
    assert ctx.sell(staff_customer["id"]).status_code == 201
    me = ctx.client.get("/sellers/me", headers=ctx.headers).json()
    assert me["customers_count"] == 2  # registered by it, or sold to by it


# --- sales ------------------------------------------------------------------------------


def test_seller_sells_only_as_itself_inside_its_region(ctx):
    holder = ctx.customer()
    # The seller is implied: the sale is the seller's own even without seller_id...
    sold = ctx.sell(holder["id"])
    assert sold.status_code == 201, sold.text
    body = sold.json()
    assert body["seller_id"] == ctx.seller["id"]
    assert body["region_name"] == "Interior SP"
    assert (body["discount_percent"], body["monthly_amount_cents"], body["commission_bp"]) == (10, 8910, 1000)
    # ...and can be neither another seller's nor nobody's.
    assert ctx.sell(ctx.customer("Two")["id"], seller_id=ctx.other_seller["id"]).status_code == 403
    direct = ctx.sell(ctx.customer("Three")["id"], seller_id=None)
    assert direct.status_code == 201 and direct.json()["seller_id"] == ctx.seller["id"]
    # The region's rules bind it like any seller's sale.
    assert ctx.sell(ctx.customer("Four")["id"], plan=ctx.other_plan).status_code == 409
    outside = ctx.sell(ctx.customer("Five", city="Niterói", state="RJ")["id"])
    assert outside.status_code == 409
    assert "outside region" in outside.json()["detail"]
    # The region's scope is the only limit: anyone inside it, whoever registered them.
    staff_customer = ctx.customer("Staff's", headers=ctx.admin)
    assert ctx.sell(staff_customer["id"]).status_code == 201
    # Its sales only, in the list and by id.
    other_sale = ctx.sell(ctx.customer("Other's", headers=ctx.admin)["id"], headers=ctx.admin, seller_id=ctx.other_seller["id"]).json()
    listed = ctx.client.get("/subscriptions", headers=ctx.headers).json()
    assert {row["seller_id"] for row in listed} == {ctx.seller["id"]}
    assert len(listed) == 3
    assert ctx.client.get(f"/subscriptions/{other_sale['id']}", headers=ctx.headers).status_code == 403
    assert ctx.client.get(f"/subscriptions/{body['id']}", headers=ctx.headers).status_code == 200
    # The quote is the seller's own too.
    quote = ctx.client.get(
        f"/subscriptions/quote?customer_id={holder['id']}&modality_id={ctx.other_plan['id']}", headers=ctx.headers
    ).json()
    assert quote["seller_id"] == ctx.seller["id"] and quote["allowed"] is False
    assert (
        ctx.client.get(
            f"/subscriptions/quote?customer_id={holder['id']}&modality_id={ctx.plan['id']}&seller_id={ctx.other_seller['id']}",
            headers=ctx.headers,
        ).status_code
        == 403
    )


def test_deactivated_seller_loses_access_at_once(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    email = f"seller{ctx.seller['id']}@example.com"
    ctx.client.put(
        f"/sellers/{ctx.seller['id']}", headers=ctx.admin, json={"name": "Ana Seller", "region_id": ctx.region["id"], "active": False}
    )
    # The token already issued stops working, and a new login is refused.
    for path in ("/auth/me", "/dashboard", f"/subscriptions/{sold['id']}", "/reports/commissions", "/customers"):
        response = ctx.client.get(path, headers=ctx.headers)
        assert response.status_code == 401, f"{path} -> {response.status_code}"
        assert response.json()["detail"] == "Your seller access was deactivated; contact the company"
    login = ctx.client.post("/auth/token", json={"email": email, "password": "saude123"})
    assert login.status_code == 401
    assert "deactivated" in login.json()["detail"]
    # The staff may still record sales attributed to it? No: an inactive seller sells nothing.
    refused = ctx.sell(ctx.customer("Later", headers=ctx.admin)["id"], headers=ctx.admin, seller_id=ctx.seller["id"])
    assert refused.status_code == 409 and "inactive" in refused.json()["detail"]
    # Its sales and commissions stay on the books for the company to settle.
    report = ctx.client.get("/reports/commissions?granularity=month&start=2026-10-01&end=2026-10-31", headers=ctx.admin).json()
    assert any(row["name"] == "Ana Seller" and row["active"] is False for row in report["by_seller"])
    # Reactivated: access is back.
    ctx.client.put(
        f"/sellers/{ctx.seller['id']}", headers=ctx.admin, json={"name": "Ana Seller", "region_id": ctx.region["id"], "active": True}
    )
    assert ctx.client.post("/auth/token", json={"email": email, "password": "saude123"}).status_code == 200
    assert ctx.client.get("/auth/me", headers=ctx.headers).status_code == 200


# --- change requests: the customer confirms ------------------------------------------------


def test_seller_edit_is_applied_only_when_the_customer_confirms(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    client_headers = auth_as(ctx.client, "client", customer_id=holder["id"])
    # An edit that changes nothing, or breaks the region's rules, is refused at once.
    nothing = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "edit", "expires_on": sold["expires_on"]})
    assert nothing.status_code == 400
    outside = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "edit", "modality_id": ctx.other_plan["id"]})
    assert outside.status_code == 409 and "not sold in region" in outside.json()["detail"]
    assert ctx.client.get(f"/subscriptions/{sold['id']}", headers=ctx.headers).json()["modality_id"] == ctx.plan["id"]

    asked = ctx.client.post(
        f"/subscriptions/{sold['id']}/changes",
        headers=ctx.headers,
        json={"kind": "edit", "expires_on": "2027-06-30", "note": "Customer asked for a shorter period"},
    )
    assert asked.status_code == 201, asked.text
    change = asked.json()
    assert change["status"] == "pending"
    assert change["summary"] == "Change: expiry 30/06/2027"
    assert change["seller_name"] == "Ana Seller"
    # Nothing changed yet; the subscription shows the pending request; a second one waits.
    current = ctx.client.get(f"/subscriptions/{sold['id']}", headers=ctx.headers).json()
    assert current["expires_on"] == "2027-09-30"
    assert current["pending_change"]["id"] == change["id"]
    again = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "cancel"})
    assert again.status_code == 409
    # The customer was asked (in-app and by email), the staff told.
    asked_client = ctx.notifications(client_headers)[0]
    assert asked_client["event"] == "sale.change_requested"
    assert asked_client["title"] == "Please confirm: change of your plan Plan Foo asked by Ana Seller"
    assert "Reason: Customer asked for a shorter period" in asked_client["body"]
    assert asked_client["link"] == f"/subscriptions/{sold['id']}"
    assert any(item["event"] == "sale.change_requested" for item in ctx.notifications(ctx.admin))
    # The seller cannot confirm its own request; another customer cannot either.
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/confirm", headers=ctx.headers).status_code == 403
    stranger = auth_as(ctx.client, "client", customer_id=ctx.customer("Stranger")["id"])
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/confirm", headers=stranger).status_code == 403
    # The customer confirms: applied, installments re-synced, seller told.
    confirmed = ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/confirm", headers=client_headers, json={"note": "ok"})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["expires_on"] == "2027-06-30"
    assert body["installments_total"] == 9
    assert body["pending_change"] is None
    assert body["seller_id"] == ctx.seller["id"]  # the sale stays the seller's, terms kept
    assert body["monthly_amount_cents"] == 8910
    history = ctx.client.get(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers).json()
    assert [(item["status"], item["decision_note"]) for item in history] == [("confirmed", "ok")]
    told = ctx.notifications(ctx.headers)[0]
    assert told["event"] == "sale.change_confirmed"
    assert told["title"] == "Request confirmed: Change: expiry 30/06/2027 — Foo Holder"
    assert "by the customer" in told["body"]
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/confirm", headers=client_headers).status_code == 409


def test_seller_cancellation_rejected_by_the_customer_or_recorded_by_the_staff(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    client_headers = auth_as(ctx.client, "client", customer_id=holder["id"])
    asked = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "cancel", "cancelled_on": "2026-10-31"})
    assert asked.status_code == 201, asked.text
    change = asked.json()
    assert change["summary"] == "Cancel on 31/10/2026"
    assert ctx.notifications(client_headers)[0]["title"] == "Please confirm: cancellation of your plan Plan Foo asked by Ana Seller"
    rejected = ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/reject", headers=client_headers, json={"note": "I want to keep it"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "pendente" and rejected.json()["cancelled_on"] is None
    told = ctx.notifications(ctx.headers)[0]
    assert told["event"] == "sale.change_rejected"
    assert "Note: I want to keep it" in told["body"] and "stays as it was" in told["body"]
    # Asked again; this time the customer answered in person and the staff records it.
    second = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "cancel"}).json()
    assert second["cancelled_on"] == "2026-10-22"  # today by default
    confirmed = ctx.client.post(f"/subscriptions/{sold['id']}/changes/{second['id']}/confirm", headers=ctx.admin)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "cancelada" and confirmed.json()["cancelled_on"] == "2026-10-22"
    latest = ctx.notifications(ctx.headers)[:2]  # both stamped in the same second
    assert {item["event"] for item in latest} == {"sale.cancelled", "sale.change_confirmed"}
    decided = next(item for item in latest if item["event"] == "sale.change_confirmed")
    assert "on the customer's word" in decided["body"]
    recorded = next(item for item in ctx.notifications(client_headers)[:2] if item["event"] == "sale.change_confirmed")
    assert "on your behalf" in recorded["title"]  # the customer is told the staff recorded its answer
    # A cancelled sale takes no further requests.
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "cancel"}).status_code == 409


def test_seller_withdraws_its_request_and_never_touches_another_sellers_sale(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    client_headers = auth_as(ctx.client, "client", customer_id=holder["id"])
    change = ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=ctx.headers, json={"kind": "edit", "modality_id": ctx.plan["id"], "joined_on": "2026-10-05"}).json()
    other = auth_as(ctx.client, "seller", seller_id=ctx.other_seller["id"])
    assert ctx.client.get(f"/subscriptions/{sold['id']}", headers=other).status_code == 403
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes", headers=other, json={"kind": "cancel"}).status_code == 403
    assert ctx.client.delete(f"/subscriptions/{sold['id']}/changes/{change['id']}", headers=other).status_code == 403
    withdrawn = ctx.client.delete(f"/subscriptions/{sold['id']}/changes/{change['id']}", headers=ctx.headers)
    assert withdrawn.status_code == 200
    assert withdrawn.json()["pending_change"] is None and withdrawn.json()["joined_on"] == "2026-10-01"
    assert ctx.notifications(client_headers)[0]["event"] == "sale.change_withdrawn"
    assert ctx.client.post(f"/subscriptions/{sold['id']}/changes/{change['id']}/confirm", headers=client_headers).status_code == 409
    # Clients and the staff still change subscriptions directly, without a request.
    direct = ctx.client.post(f"/subscriptions/{sold['id']}/cancel", headers=client_headers)
    assert direct.status_code == 200 and direct.json()["status"] == "cancelada"


# --- reports and home -------------------------------------------------------------------


def pay(ctx, subscription, index, paid_on):
    item = subscription["installments"][index]
    response = ctx.client.put(
        f"/subscriptions/{subscription['id']}/installments/{item['id']}", headers=ctx.admin, json={"paid_on": paid_on.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_seller_reports_are_scoped_to_itself(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    pay(ctx, sold, 0, date(2026, 10, 1))
    pending = ctx.sell(ctx.customer("Pending")["id"]).json()
    other_sale = ctx.sell(ctx.customer("Other's", headers=ctx.admin)["id"], headers=ctx.admin, seller_id=ctx.other_seller["id"]).json()
    pay(ctx, other_sale, 0, date(2026, 10, 2))
    query = "granularity=month&start=2026-10-01&end=2026-10-31"

    report = ctx.client.get(f"/reports/commissions?{query}", headers=ctx.headers).json()
    assert report["totals"]["sold"] == 2
    assert report["totals"]["commission_cents"] == 891  # only Ana's payment
    assert [row["name"] for row in report["by_seller"]] == ["Ana Seller"]
    assert report["portfolio"] == {
        "customers": 2,
        "subscriptions": 2,
        "active": 1,
        "pending": 1,
        "overdue": 0,
        "expired": 0,
        "cancelled": 0,
        "monthly_cents": 8910,
        "installments_due_week": 0,  # the active plan's next installment (2026-11-01) is further than 7 days
        "installments_overdue": 1,  # the pending plan's first installment (2026-10-01)
    }
    # Asking for another seller is refused; the statement is its own.
    assert ctx.client.get(f"/reports/commissions?{query}&seller_id={ctx.other_seller['id']}", headers=ctx.headers).status_code == 403
    assert ctx.client.get(f"/reports/commissions/{ctx.other_seller['id']}?{query}", headers=ctx.headers).status_code == 403
    statement = ctx.client.get(f"/reports/commissions/{ctx.seller['id']}?{query}", headers=ctx.headers).json()
    assert statement["commission_cents"] == 891 and len(statement["lines"]) == 1
    # The admin still sees everyone, with the portfolio of the filter.
    everyone = ctx.client.get(f"/reports/commissions?{query}", headers=ctx.admin).json()
    assert everyone["totals"]["commission_cents"] == 891 * 2
    assert everyone["portfolio"]["subscriptions"] == 3
    assert sorted(row["name"] for row in everyone["by_seller"]) == ["Ana Seller", "Bia Seller"]


def test_seller_home_shows_its_sales_portfolio_and_commission(ctx):
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    pay(ctx, sold, 0, date(2026, 10, 1))
    pay(ctx, sold, 1, date(2026, 10, 20))
    ctx.sell(ctx.customer("Pending")["id"])
    home = ctx.client.get("/dashboard", headers=ctx.headers).json()
    assert home["role"] == "seller"
    assert home["seller_name"] == "Ana Seller"
    assert home["region_name"] == "Interior SP"
    assert home["region_commission_bp"] == 1000
    assert home["sales_total"] == 2 and home["sales_month"] == 2
    assert home["customers_count"] == 2
    assert home["commission_month_cents"] == 2 * 891
    assert home["commission_total_cents"] == 2 * 891
    assert (home["subscriptions_active"], home["subscriptions_pending"]) == (1, 1)
    assert home["appointments_today"] == 0 and home["next_appointments"] == []
    assert [(row["customer_name"], row["status"], row["monthly_amount_cents"]) for row in home["recent_sales"]] == [
        ("Pending", "pendente", 8910),
        ("Foo Holder", "ativa", 8910),
    ]


# --- notifications ---------------------------------------------------------------------


def test_seller_is_notified_of_sales_commissions_and_region_changes(ctx):
    other = auth_as(ctx.client, "seller", seller_id=ctx.other_seller["id"])
    holder = ctx.customer()
    sold = ctx.sell(holder["id"]).json()
    mine = ctx.notifications(ctx.headers)
    assert [item["event"] for item in mine] == ["sale.recorded", "user.created"]  # newest first
    assert mine[0]["title"] == "Sale recorded: Foo Holder — Plan Foo"
    assert "R$ 89,10 (10% special discount of region Interior SP)" in mine[0]["body"]
    assert mine[0]["link"] == f"/subscriptions/{sold['id']}"
    # The staff knows what the seller did on its own...
    staff = ctx.notifications(ctx.admin)
    assert any(item["title"] == "Ana Seller sold Plan Foo to Foo Holder" for item in staff)
    # ...but not when the staff itself recorded the sale.
    ctx.sell(ctx.customer("By staff", headers=ctx.admin)["id"], headers=ctx.admin, seller_id=ctx.seller["id"])
    assert sum(1 for item in ctx.notifications(ctx.admin) if item["event"] == "sale.recorded") == 1
    assert sum(1 for item in ctx.notifications(ctx.headers) if item["event"] == "sale.recorded") == 2
    # Emails go to the seller's address and its login.
    recipients = {mail["to"] for mail in ctx.outbox.sent if mail["subject"].startswith("Sale recorded")}
    assert recipients == set()  # queued, not sent yet
    ctx.client.post("/queue/run", headers=ctx.admin)
    recipients = {mail["to"] for mail in ctx.outbox.sent if mail["subject"].startswith("Sale recorded")}
    assert recipients == {"ana@example.com", f"seller{ctx.seller['id']}@example.com"}

    pay(ctx, sold, 0, date(2026, 10, 1))
    latest = ctx.notifications(ctx.headers)[0]
    assert latest["event"] == "commission.earned"
    assert latest["title"] == "Commission earned: R$ 8,91 — Foo Holder, installment 1"
    assert latest["link"] == "/my-commissions"

    ctx.client.post(f"/subscriptions/{sold['id']}/cancel", headers=ctx.admin)
    latest = ctx.notifications(ctx.headers)[0]
    assert latest["event"] == "sale.cancelled"
    assert "no further commission" in latest["body"]

    # Region rules: every seller of the region, only when the rules really change.
    before = len(ctx.notifications(ctx.headers))
    ctx.client.put(
        f"/regions/{ctx.region['id']}",
        headers=ctx.admin,
        json={"name": "Interior SP", "description": "renamed only", "commission_bp": 1000, "areas": [{"state": "SP"}], "plans": [{"modality_id": ctx.plan["id"], "discount_percent": 10}]},
    )
    assert len(ctx.notifications(ctx.headers)) == before
    ctx.client.put(
        f"/regions/{ctx.region['id']}",
        headers=ctx.admin,
        json={"name": "Interior SP", "commission_bp": 1200, "areas": [{"state": "SP"}, {"state": "MG"}], "plans": [{"modality_id": ctx.plan["id"], "discount_percent": 5}]},
    )
    latest = ctx.notifications(ctx.headers)[0]
    assert latest["event"] == "region.updated"
    assert "Commission: 12% on the plan fees received" in latest["body"]
    assert "Scope: MG, SP" in latest["body"]
    assert "Plan Foo (5% off)" in latest["body"]
    assert ctx.notifications(other)[0]["event"] == "region.updated"

    # Moved to another region, deactivated.
    rio = ctx.client.post("/regions", headers=ctx.admin, json={"name": "Rio", "commission_bp": 500, "areas": [{"state": "RJ"}]}).json()
    ctx.client.put(f"/sellers/{ctx.seller['id']}", headers=ctx.admin, json={"name": "Ana Seller", "region_id": rio["id"], "active": True, "email": "ana@example.com"})
    latest = ctx.notifications(ctx.headers)[0]
    assert latest["event"] == "seller.moved"
    assert latest["title"] == "You now sell in region Rio"
    assert "Moved from Interior SP" in latest["body"]
    # Deactivated: access is gone at once, so the notice reaches the seller by email.
    ctx.client.put(f"/sellers/{ctx.seller['id']}", headers=ctx.admin, json={"name": "Ana Seller", "region_id": rio["id"], "active": False, "email": "ana@example.com"})
    assert ctx.client.get("/notifications", headers=ctx.headers).status_code == 401
    ctx.client.post("/queue/run", headers=ctx.admin)
    notices = [mail for mail in ctx.outbox.sent if mail["subject"] == "Your seller access was deactivated"]
    assert {mail["to"] for mail in notices} == {"ana@example.com", f"seller{ctx.seller['id']}@example.com"}
    assert "settled by the company" in notices[0]["body"]
    # Nothing for the other seller about Ana's move.
    assert ctx.notifications(other)[0]["event"] == "region.updated"


def test_seller_login_welcome_mentions_its_region(ctx):
    created = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "Bia", "email": "bia.login@example.com", "role": "seller", "password": "saude123", "seller_id": ctx.other_seller["id"]},
    )
    assert created.status_code == 201, created.text
    bia = auth(ctx.client, "bia.login@example.com")
    welcome = ctx.notifications(bia)[0]
    assert welcome["event"] == "user.created"
    assert "You sell as Bia Seller in region Interior SP" in welcome["body"]

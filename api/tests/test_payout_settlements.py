"""Advances, settlements with installments, receipts (digital + PDF) and
their confirmation, for professionals and clinics.

Flow: signed appointments accrue → an advance may be paid ahead → a
settlement closes the account up to a date (earned − advances = net, split
into installments) → each installment paid gets a receipt → the professional
signs it (a clinic's receipt is acknowledged by the staff) → reports show
what was handed over and the position of each payee.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import DUMMY_SIGN_BODY, activate_plan, auth, auth_as, ensure_plan, sign, sign_body
from app.payouts import (
    MAX_INSTALLMENTS,
    PAYEE_CLINIC,
    PAYEE_PROFESSIONAL,
    STATUS_AWAITING_SIGNATURE,
    STATUS_OPEN,
    STATUS_SETTLED,
    STATUS_VOID,
    PayoutError,
    apply_advances,
    fee_for,
    fingerprint,
    next_receipt_number,
    plan_settlement,
    position,
    settlement_status,
    split_installments,
    unsettled,
)

W43_MON = datetime(2026, 10, 19, 8, 0)
W43_TUE = datetime(2026, 10, 20, 8, 0)
W44_MON = datetime(2026, 10, 26, 8, 0)
OCT = "start=2026-10-01&end=2026-10-31"


# --- pure rules -----------------------------------------------------------------------


def appt(id, fee=5000, clinic_fee=2000, status="realizada", marked_at=W43_MON, pro_settled=None, clinic_settled=None):
    return SimpleNamespace(
        id=id,
        status=status,
        professional_price_cents=fee,
        fee_cents=clinic_fee,
        marked_at=marked_at,
        professional_settlement_id=pro_settled,
        clinic_settlement_id=clinic_settled,
    )


def advance(id, amount, applied=0, paid_on=date(2026, 10, 1)):
    return SimpleNamespace(id=id, kind="advance", amount_cents=amount, applied_cents=applied, paid_on=paid_on, signed_at=None, acknowledged_at=None)


def test_fee_for_each_payee_only_on_signed_appointments():
    assert fee_for(appt(1), PAYEE_PROFESSIONAL) == 5000
    assert fee_for(appt(1), PAYEE_CLINIC) == 2000
    assert fee_for(appt(1, status="agendada"), PAYEE_PROFESSIONAL) == 0


def test_unsettled_filters_by_payee_and_cutoff():
    rows = [appt(1), appt(2, pro_settled=9), appt(3, marked_at=W44_MON), appt(4, status="agendada")]
    assert [r.id for r in unsettled(rows, PAYEE_PROFESSIONAL)] == [1, 3]
    assert [r.id for r in unsettled(rows, PAYEE_PROFESSIONAL, until=date(2026, 10, 25))] == [1]
    assert [r.id for r in unsettled(rows, PAYEE_CLINIC)] == [1, 2, 3]  # settled for the professional, not for the clinic


def test_advances_are_applied_oldest_first_and_carry_over():
    older = advance(2, 3000, paid_on=date(2026, 10, 1))
    newer = advance(1, 4000, paid_on=date(2026, 10, 5))
    applied = apply_advances([newer, older], earned_cents=5000)
    assert [(a.advance.id, a.amount_cents) for a in applied] == [(2, 3000), (1, 2000)]  # 2000 of the newer one carries over
    assert apply_advances([advance(3, 1000, applied=1000)], 5000) == []  # fully applied already
    assert apply_advances([older], 0) == []


def test_installments_split_equally_with_the_cents_on_the_first():
    plan = split_installments(10001, 3, date(2026, 11, 1), 30)
    assert plan == [(1, 3335, date(2026, 11, 1)), (2, 3333, date(2026, 12, 1)), (3, 3333, date(2026, 12, 31))]
    assert sum(a for _, a, _ in plan) == 10001
    assert split_installments(0, 3, date(2026, 11, 1), 30) == []  # nothing to pay
    with pytest.raises(PayoutError):
        split_installments(100, 0, date(2026, 11, 1), 30)
    with pytest.raises(PayoutError):
        split_installments(100, MAX_INSTALLMENTS + 1, date(2026, 11, 1), 30)
    with pytest.raises(PayoutError):
        split_installments(100, 2, date(2026, 11, 1), 0)


def test_plan_settlement():
    rows = [appt(1), appt(2, marked_at=W43_TUE), appt(3, marked_at=W44_MON)]
    plan = plan_settlement(rows, [advance(1, 3000)], PAYEE_PROFESSIONAL, date(2026, 10, 25), 2, date(2026, 11, 10), 15)
    assert [r.id for r in plan.appointments] == [1, 2]
    assert (plan.earned_cents, plan.advances_cents, plan.net_cents) == (10000, 3000, 7000)
    assert plan.period_start == date(2026, 10, 19) and plan.period_end == date(2026, 10, 25)
    assert [(k, a, d) for k, a, d in plan.installments] == [(1, 3500, date(2026, 11, 10)), (2, 3500, date(2026, 11, 25))]
    with pytest.raises(PayoutError, match="Nothing to settle"):
        plan_settlement(rows, [], PAYEE_PROFESSIONAL, date(2026, 10, 1), 1, date(2026, 11, 1), 30)
    # Advances larger than the earnings: net zero, no installments, the rest carries over.
    covered = plan_settlement(rows, [advance(1, 50000)], PAYEE_PROFESSIONAL, date(2026, 10, 31), 3, date(2026, 11, 1), 30)
    assert (covered.earned_cents, covered.advances_cents, covered.net_cents) == (15000, 15000, 0)
    assert covered.installments == []


def payment(paid_on=None, signed=None, acknowledged=None, kind="installment", amount=100, applied=0):
    return SimpleNamespace(kind=kind, amount_cents=amount, paid_on=paid_on, signed_at=signed, acknowledged_at=acknowledged, applied_cents=applied)


def test_settlement_status():
    s = SimpleNamespace(voided_at=None, payments=[payment(), payment(paid_on=date(2026, 11, 1))], earned_cents=0)
    assert settlement_status(s) == STATUS_OPEN
    s.payments = [payment(paid_on=date(2026, 11, 1)), payment(paid_on=date(2026, 11, 1), signed=datetime(2026, 11, 2))]
    assert settlement_status(s) == STATUS_AWAITING_SIGNATURE
    s.payments = [payment(paid_on=date(2026, 11, 1), signed=datetime(2026, 11, 2)), payment(paid_on=date(2026, 11, 1), acknowledged=datetime(2026, 11, 2))]
    assert settlement_status(s) == STATUS_SETTLED
    s.payments = []
    assert settlement_status(s) == STATUS_SETTLED  # fully covered by advances
    s.voided_at = datetime(2026, 11, 3)
    assert settlement_status(s) == STATUS_VOID


def test_receipt_numbers_are_sequential_per_year():
    assert next_receipt_number([], date(2026, 11, 1)) == "REC-2026-000001"
    assert next_receipt_number(["REC-2026-000001", "REC-2026-000007", "REC-2025-000099", None], date(2026, 11, 1)) == "REC-2026-000008"
    assert next_receipt_number(["REC-2026-000007"], date(2027, 1, 1)) == "REC-2027-000001"


def test_fingerprint_changes_with_the_content():
    p = SimpleNamespace(receipt_number="REC-2026-000001", kind="advance", payee_type="professional", amount_cents=100, paid_on=date(2026, 11, 1), method="pix", signed_at=None, acknowledged_at=None, received_by="")
    before = fingerprint(p, "Lorem")
    assert len(before) == 16
    assert fingerprint(p, "Lorem") == before
    p.signed_at = datetime(2026, 11, 2, 10, 0)
    assert fingerprint(p, "Lorem") != before
    assert fingerprint(p, "Other") != fingerprint(p, "Lorem")


def test_position():
    rows = [appt(1), appt(2, pro_settled=1)]
    payments = [advance(1, 3000, applied=1000), payment(paid_on=None, amount=2000), payment(paid_on=date(2026, 11, 1), amount=1500)]
    settlements = [SimpleNamespace(voided_at=None, earned_cents=5000), SimpleNamespace(voided_at=datetime(2026, 1, 1), earned_cents=999)]
    pos = position(rows, payments, settlements, PAYEE_PROFESSIONAL)
    assert (pos.unsettled_appointments, pos.unsettled_cents) == (1, 5000)
    assert pos.advances_outstanding_cents == 2000
    assert (pos.open_installments, pos.open_installments_cents) == (1, 2000)
    assert (pos.receipts_to_confirm, pos.receipts_to_confirm_cents) == (2, 4500)  # the advance and the paid installment
    assert pos.paid_cents == 4500
    assert pos.settled_cents == 5000
    assert pos.balance_cents == 5000 + 2000 - 2000


# --- API ------------------------------------------------------------------------------


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin, discount_percent=20)
    lorem = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    dolor = client.post(
        "/professionals", headers=admin, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 7000}
    ).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)
    activate_plan(client, admin, other, plan=plan)

    def book(customer, professional, when, signed=True):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": customer, "professional_id": professional, "marked_at": when.isoformat()},
        )
        assert response.status_code == 201, response.text
        if signed:
            sign(client, response.json()["id"])
        return response.json()

    c = SimpleNamespace(client=client, admin=admin, lorem=lorem, dolor=dolor, holder=holder, other=other, book=book)
    # Lorem: three signed appointments in October (15000 for Lorem, 3 x 2000 for Clinic Foo) and one scheduled.
    c.a1 = book(holder, lorem, W43_MON)
    c.a2 = book(other, lorem, W43_TUE)
    c.a3 = book(holder, lorem, W44_MON)
    c.a4 = book(other, lorem, W44_MON + timedelta(hours=1), signed=False)
    # Dolor: one signed (7000 for Dolor, 2000 for Clinic Foo).
    c.d1 = book(holder, dolor, W43_TUE + timedelta(hours=1))
    c.lorem_login = auth_as(client, "professional", professional_id=lorem)
    c.dolor_login = auth_as(client, "professional", professional_id=dolor)
    c.pro = {"payee_type": "professional", "payee_id": lorem}
    c.pro_q = f"payee_type=professional&payee_id={lorem}"
    c.clinic_q = "payee_type=clinic&payee_id=1"
    return c


def test_payees_and_position(ctx):
    payees = ctx.client.get("/payouts/payees", headers=ctx.admin).json()
    kinds = {(p["payee_type"], p["name"]) for p in payees}
    assert ("professional", "Lorem Ipsum") in kinds and ("clinic", "Clinic Foo") in kinds
    lorem = next(p for p in payees if p["name"] == "Lorem Ipsum")
    assert lorem["has_login"] is True and lorem["detail"] == "Clínico geral · Clinic Foo"
    pos = ctx.client.get(f"/payouts/position?{ctx.pro_q}", headers=ctx.admin).json()
    assert (pos["unsettled_appointments"], pos["unsettled_cents"], pos["balance_cents"]) == (3, 15000, 15000)
    assert pos["advances_outstanding_cents"] == 0 and pos["paid_cents"] == 0
    clinic = ctx.client.get(f"/payouts/position?{ctx.clinic_q}", headers=ctx.admin).json()
    assert clinic["payee"]["name"] == "Clinic Foo"
    assert (clinic["unsettled_appointments"], clinic["unsettled_cents"]) == (4, 8000)  # Lorem's 3 + Dolor's 1


def test_advance_then_settlement_in_installments_with_receipts(ctx):
    # 1. An advance of 4000 to Lorem, with a receipt to sign.
    advance = ctx.client.post(
        "/payouts/advances",
        headers=ctx.admin,
        json={**ctx.pro, "amount_cents": 4000, "paid_on": "2026-10-22", "method": "pix", "reference": "E2E-1", "notes": "Requested by the professional"},
    )
    assert advance.status_code == 201, advance.text
    adv = advance.json()
    assert adv["kind"] == "advance" and adv["receipt_number"] == "REC-2026-000001"
    assert adv["outstanding_cents"] == 4000 and adv["confirmed"] is False and adv["fingerprint"]
    assert adv["registered_by_name"] == "Foo Bar"
    pos = ctx.client.get(f"/payouts/position?{ctx.pro_q}", headers=ctx.admin).json()
    assert pos["advances_outstanding_cents"] == 4000 and pos["balance_cents"] == 11000 and pos["receipts_to_confirm"] == 1
    # The professional is told and must sign.
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.lorem_login).json()]
    assert titles[0].startswith("Advance of R$ 40,00 paid on 22/10/2026")

    # 2. Settlement up to 25/10: two signed appointments (10000) − advance 4000 = 6000 in 2 installments.
    created = ctx.client.post(
        "/payouts/settlements",
        headers=ctx.admin,
        json={**ctx.pro, "until": "2026-10-25", "installments": 2, "first_due_on": "2026-11-05", "interval_days": 30, "notes": "October, first half"},
    )
    assert created.status_code == 201, created.text
    settlement = created.json()
    assert settlement["status"] == "open"
    assert (settlement["appointments_count"], settlement["earned_cents"], settlement["advances_cents"], settlement["net_cents"]) == (2, 10000, 4000, 6000)
    assert settlement["period_start"] == "2026-10-19" and settlement["period_end"] == "2026-10-25"
    assert [(i["sequence"], i["amount_cents"], i["due_on"], i["paid_on"]) for i in settlement["installments"]] == [
        (1, 3000, "2026-11-05", None),
        (2, 3000, "2026-12-05", None),
    ]
    assert [a["amount_cents"] for a in settlement["advances_applied"]] == [4000]
    assert [a["appointment_id"] for a in settlement["appointments"]] == [ctx.a1["id"], ctx.a2["id"]]
    assert settlement["appointments"][0]["fee_cents"] == 5000
    # The advance is now fully applied; the third appointment stays unsettled.
    pos = ctx.client.get(f"/payouts/position?{ctx.pro_q}", headers=ctx.admin).json()
    assert (pos["unsettled_cents"], pos["advances_outstanding_cents"], pos["open_installments_cents"], pos["balance_cents"]) == (5000, 0, 6000, 11000)
    assert pos["settled_cents"] == 10000
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.lorem_login).json()]
    assert titles[0].startswith("Settlement 19/10/2026 – 25/10/2026: R$ 60,00 to receive")

    # 3. Pay the first installment: a receipt is issued.
    first, second = settlement["installments"]
    paid = ctx.client.put(
        f"/payouts/settlements/{settlement['id']}/installments/{first['id']}",
        headers=ctx.admin,
        json={"paid_on": "2026-11-05", "method": "transfer", "reference": "TED 123"},
    )
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["status"] == "open" and body["paid_cents"] == 3000 and body["confirmed_cents"] == 0
    receipt1 = body["installments"][0]
    assert receipt1["receipt_number"] == "REC-2026-000002" and receipt1["paid_on"] == "2026-11-05"
    assert receipt1["installments_count"] == 2
    again = ctx.client.put(f"/payouts/settlements/{settlement['id']}/installments/{first['id']}", headers=ctx.admin, json={"paid_on": "2026-11-06"})
    assert again.status_code == 409
    assert not body["can_void"]  # something was paid

    # 4. The professional signs the receipts (advance + installment); the admin is told.
    mine = ctx.client.get("/payouts/receipts?unconfirmed=true", headers=ctx.lorem_login).json()
    assert [r["receipt_number"] for r in mine] == ["REC-2026-000002", "REC-2026-000001"]
    assert all(r["can_sign"] for r in mine)
    signed = ctx.client.post(f"/payouts/receipts/{receipt1['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert signed.status_code == 200, signed.text
    receipt = signed.json()
    assert receipt["payment"]["signed_by_name"] == f"Professional {ctx.lorem}" and receipt["payment"]["confirmed"] is True
    assert receipt["title"] == "Payout receipt" and "Installment 1 of 2" in receipt["description"]
    assert receipt["settlement"]["id"] == settlement["id"]
    assert receipt["fingerprint"] == receipt["payment"]["fingerprint"]
    assert ctx.client.post(f"/payouts/receipts/{receipt1['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 409
    assert ctx.client.post(f"/payouts/receipts/{adv['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 200
    admin_titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.admin).json()]
    assert admin_titles[0].startswith("Receipt REC-2026-000001 signed by")

    # 5. Pay the second installment and sign: settled.
    paid2 = ctx.client.put(f"/payouts/settlements/{settlement['id']}/installments/{second['id']}", headers=ctx.admin, json={"paid_on": "2026-12-05", "method": "pix"})
    assert paid2.json()["status"] == "awaiting_signature"
    ctx.client.post(f"/payouts/receipts/{second['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    final = ctx.client.get(f"/payouts/settlements/{settlement['id']}", headers=ctx.lorem_login).json()
    assert final["status"] == "settled" and final["confirmed_cents"] == 6000
    pos = ctx.client.get("/payouts/position", headers=ctx.lorem_login).json()
    assert pos["receipts_to_confirm"] == 0 and pos["paid_cents"] == 10000 and pos["balance_cents"] == 5000

    # 6. A second settlement takes what was left (the third appointment), in one installment.
    later = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-30"}).json()
    assert (later["appointments_count"], later["earned_cents"], later["advances_cents"], later["net_cents"]) == (1, 5000, 0, 5000)
    assert ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-30"}).status_code == 409


def test_receipt_pdf(ctx):
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 12345, "paid_on": "2026-10-22", "method": "cash"}).json()
    response = ctx.client.get(f"/payouts/receipts/{adv['id']}.pdf", headers=ctx.lorem_login)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="receipt_REC-2026-000001.pdf"' in response.headers["content-disposition"]
    data = response.content
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"REC-2026-000001" in data and b"ADVANCE RECEIPT" in data
    assert b"Lorem Ipsum" in data and b"R$ 123,45" in data
    assert b"NOT CONFIRMED YET" in data
    ctx.client.post(f"/payouts/receipts/{adv['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    signed = ctx.client.get(f"/payouts/receipts/{adv['id']}.pdf", headers=ctx.admin).content
    assert b"Digitally signed by" in signed and b"NOT CONFIRMED" not in signed
    # The fingerprint printed is the one the API reports.
    receipt = ctx.client.get(f"/payouts/receipts/{adv['id']}", headers=ctx.admin).json()
    assert receipt["fingerprint"].encode() in signed


def test_clinic_advance_and_acknowledgement(ctx):
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "clinic", "payee_id": 1, "amount_cents": 3000, "paid_on": "2026-10-22", "method": "transfer"})
    assert adv.status_code == 201, adv.text
    body = adv.json()
    assert body["payee"]["payee_type"] == "clinic" and body["payee"]["name"] == "Clinic Foo"
    assert body["can_sign"] is False and body["can_acknowledge"] is True
    # A professional cannot sign a clinic's receipt; the admin acknowledges who received it.
    assert ctx.client.post(f"/payouts/receipts/{body['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 403
    ack = ctx.client.post(f"/payouts/receipts/{body['id']}/acknowledge", headers=ctx.admin, json={"received_by": "Maria Manager", "received_by_document": "123.456.789-00"})
    assert ack.status_code == 200, ack.text
    assert ack.json()["payment"]["confirmed"] is True and ack.json()["payment"]["received_by"] == "Maria Manager"
    pdf = ctx.client.get(f"/payouts/receipts/{body['id']}.pdf", headers=ctx.admin).content
    assert b"Received by Maria Manager" in pdf and b"123.456.789-00" in pdf  # "(...)" is escaped in PDF strings
    assert ctx.client.post(f"/payouts/receipts/{body['id']}/acknowledge", headers=ctx.admin, json={"received_by": "X"}).status_code == 409
    # Clinic settlement: 4 signed appointments x 2000 − 3000 advance = 5000 in 3 installments.
    settlement = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={"payee_type": "clinic", "payee_id": 1, "until": "2026-10-31", "installments": 3, "first_due_on": "2026-11-10", "interval_days": 15}).json()
    assert (settlement["appointments_count"], settlement["earned_cents"], settlement["advances_cents"], settlement["net_cents"]) == (4, 8000, 3000, 5000)
    assert [i["amount_cents"] for i in settlement["installments"]] == [1668, 1666, 1666]
    assert [i["due_on"] for i in settlement["installments"]] == ["2026-11-10", "2026-11-25", "2026-12-10"]
    # A professional's receipt cannot be acknowledged by the staff: the professional signs.
    pro_adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 100, "paid_on": "2026-10-22"}).json()
    assert ctx.client.post(f"/payouts/receipts/{pro_adv['id']}/acknowledge", headers=ctx.admin, json={"received_by": "X"}).status_code == 409


def test_void_settlement_releases_appointments_and_advances(ctx):
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 2000, "paid_on": "2026-10-22"}).json()
    settlement = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 2, "first_due_on": "2026-11-05"}).json()
    assert settlement["can_void"] is True
    assert ctx.client.get(f"/payouts/advances?{ctx.pro_q}", headers=ctx.admin).json()[0]["outstanding_cents"] == 0
    # Nothing left to settle while it stands...
    assert ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-05"}).status_code == 409
    assert ctx.client.post(f"/payouts/settlements/{settlement['id']}/void", headers=ctx.lorem_login).status_code == 403
    voided = ctx.client.post(f"/payouts/settlements/{settlement['id']}/void", headers=ctx.admin)
    assert voided.status_code == 200 and voided.json()["status"] == "void" and voided.json()["installments"] == []
    # ...and everything is back once voided.
    pos = ctx.client.get(f"/payouts/position?{ctx.pro_q}", headers=ctx.admin).json()
    assert pos["unsettled_cents"] == 15000 and pos["advances_outstanding_cents"] == 2000 and pos["open_installments_cents"] == 0
    assert ctx.client.get(f"/payouts/advances?{ctx.pro_q}", headers=ctx.admin).json()[0]["outstanding_cents"] == 2000
    again = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-05"}).json()
    assert again["net_cents"] == 13000
    # Paid installments block the void.
    ctx.client.put(f"/payouts/settlements/{again['id']}/installments/{again['installments'][0]['id']}", headers=ctx.admin, json={"paid_on": "2026-11-05"})
    assert ctx.client.post(f"/payouts/settlements/{again['id']}/void", headers=ctx.admin).status_code == 409


def test_advance_deletion_rules(ctx):
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 2000, "paid_on": "2026-10-22"}).json()
    assert adv["can_delete"] is True
    assert ctx.client.delete(f"/payouts/advances/{adv['id']}", headers=ctx.admin).status_code == 204
    assert ctx.client.get(f"/payouts/advances?{ctx.pro_q}", headers=ctx.admin).json() == []
    signed = ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 2000, "paid_on": "2026-10-22"}).json()
    ctx.client.post(f"/payouts/receipts/{signed['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert ctx.client.delete(f"/payouts/advances/{signed['id']}", headers=ctx.admin).status_code == 409  # confirmed: money changed hands
    assert ctx.client.delete("/payouts/advances/9999", headers=ctx.admin).status_code == 404


def test_validation(ctx):
    assert ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 0, "paid_on": "2026-10-22"}).status_code == 422
    assert ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "patient", "payee_id": 1, "amount_cents": 100, "paid_on": "2026-10-22"}).status_code == 422
    assert ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 100, "paid_on": "2026-10-22", "method": "gold"}).status_code == 400
    assert ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "professional", "payee_id": 9999, "amount_cents": 100, "paid_on": "2026-10-22"}).status_code == 404
    assert ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 13, "first_due_on": "2026-11-05"}).status_code == 422
    assert ctx.client.get("/payouts/position?payee_type=professional", headers=ctx.admin).status_code == 400
    assert ctx.client.get(f"/payouts/receipts/{ctx.a4['id']}", headers=ctx.admin).status_code == 404


def test_professional_sees_only_its_own_account(ctx):
    ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 1000, "paid_on": "2026-10-22"})
    dolor_adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "professional", "payee_id": ctx.dolor, "amount_cents": 2000, "paid_on": "2026-10-22"}).json()
    mine = ctx.client.get("/payouts/advances", headers=ctx.lorem_login).json()
    assert [a["amount_cents"] for a in mine] == [1000]
    # Filters cannot widen the scope.
    assert ctx.client.get(f"/payouts/advances?payee_type=professional&payee_id={ctx.dolor}", headers=ctx.lorem_login).json() == []
    assert ctx.client.get(f"/payouts/position?payee_type=professional&payee_id={ctx.dolor}", headers=ctx.lorem_login).json()["payee"]["payee_id"] == ctx.lorem
    assert ctx.client.get(f"/payouts/receipts/{dolor_adv['id']}", headers=ctx.lorem_login).status_code == 403
    assert ctx.client.get(f"/payouts/receipts/{dolor_adv['id']}.pdf", headers=ctx.lorem_login).status_code == 403
    assert ctx.client.post(f"/payouts/receipts/{dolor_adv['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 403
    dolor_settlement = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={"payee_type": "professional", "payee_id": ctx.dolor, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-05"}).json()
    assert ctx.client.get(f"/payouts/settlements/{dolor_settlement['id']}", headers=ctx.lorem_login).status_code == 403
    assert ctx.client.get("/payouts/settlements", headers=ctx.lorem_login).json() == []
    # Professionals never manage: no advances, settlements, payments, acknowledgements or payee lists.
    assert ctx.client.post("/payouts/advances", headers=ctx.lorem_login, json={**ctx.pro, "amount_cents": 1, "paid_on": "2026-10-22"}).status_code == 403
    assert ctx.client.post("/payouts/settlements", headers=ctx.lorem_login, json={**ctx.pro, "until": "2026-10-31", "installments": 1, "first_due_on": "2026-11-05"}).status_code == 403
    assert ctx.client.get("/payouts/payees", headers=ctx.lorem_login).status_code == 403


@pytest.mark.parametrize("role", ["supervisor", "attendant", "client"])
def test_other_roles_have_no_access(ctx, role):
    headers = auth_as(ctx.client, role, customer_id=ctx.holder)
    for path in ("/payouts/payees", f"/payouts/position?{ctx.pro_q}", "/payouts/advances", "/payouts/settlements", "/payouts/receipts"):
        assert ctx.client.get(path, headers=headers).status_code == 403, path


def test_reports_show_payments_and_position(ctx):
    ctx.client.post("/payouts/advances", headers=ctx.admin, json={**ctx.pro, "amount_cents": 4000, "paid_on": "2026-10-22"})
    settlement = ctx.client.post("/payouts/settlements", headers=ctx.admin, json={**ctx.pro, "until": "2026-10-31", "installments": 2, "first_due_on": "2026-10-28", "interval_days": 30}).json()
    first = settlement["installments"][0]
    ctx.client.put(f"/payouts/settlements/{settlement['id']}/installments/{first['id']}", headers=ctx.admin, json={"paid_on": "2026-10-28"})
    ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "clinic", "payee_id": 1, "amount_cents": 1000, "paid_on": "2026-10-29"})

    body = ctx.client.get(f"/reports/payouts?granularity=week&{OCT}", headers=ctx.admin).json()
    lorem = next(r for r in body["professionals"] if r["name"] == "Lorem Ipsum")
    assert lorem["payout_cents"] == 15000  # earned in October
    assert (lorem["advances_cents"], lorem["installments_cents"], lorem["paid_cents"]) == (4000, 5500, 9500)
    assert lorem["unconfirmed_receipts"] == 2
    assert (lorem["unsettled_cents"], lorem["advances_outstanding_cents"], lorem["open_installments_cents"], lorem["balance_cents"]) == (0, 0, 5500, 5500)
    week43 = next(p for p in lorem["periods"] if p["period"] == "2026-W43")
    week44 = next(p for p in lorem["periods"] if p["period"] == "2026-W44")
    assert (week43["paid_cents"], week44["paid_cents"]) == (4000, 5500)
    assert body["totals"]["paid_cents"] == 9500 and body["totals"]["unconfirmed_receipts"] == 2
    # The professional's own report carries the same numbers.
    own = ctx.client.get(f"/reports/payouts?{OCT}", headers=ctx.lorem_login).json()["professionals"][0]
    assert own["paid_cents"] == 9500 and own["balance_cents"] == 5500

    finance = ctx.client.get(f"/reports/finance?granularity=month&{OCT}", headers=ctx.admin).json()
    totals = finance["totals"]
    assert (totals["payouts_professionals_cents"], totals["payouts_clinics_cents"], totals["payouts_cents"]) == (9500, 1000, 10500)
    assert totals["result_cents"] == totals["net_cents"] - 10500
    assert finance["periods"][0]["payouts_cents"] == 10500

    dashboard = ctx.client.get("/dashboard", headers=ctx.admin).json()
    assert dashboard["receipts_unconfirmed"] == 3
    mine = ctx.client.get("/dashboard", headers=ctx.lorem_login).json()
    assert mine["receipts_to_sign"] == 2 and mine["balance_cents"] == 5500

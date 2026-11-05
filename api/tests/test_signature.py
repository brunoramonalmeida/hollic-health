"""The professional's signature completes an appointment.

Nobody types "completed": the reception's check-out leaves the appointment
awaiting signature, and only the login of the appointment's professional can
sign it — after the slot started, never on a cancelled one, never twice. The
professional may revoke its own signature; the staff may cancel a signed
appointment (the signature is dropped); everything else on a signed
appointment is frozen.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import DUMMY_SIGN_BODY, activate_plan, auth, auth_as, ensure_plan, revoke_body, sign_body, start
from app import clock
from app.signature import (
    SIGNATURE_REQUIRED,
    SIGNED_FROZEN,
    SignatureError,
    assert_can_revoke,
    assert_can_sign,
    awaiting_signature,
    can_sign,
    check_signed_edit,
    check_status_change,
    clear_signature,
    revoke,
    sign,
)

NOW = datetime(2026, 10, 26, 10, 0)


def grid(moment: datetime) -> datetime:
    """Floor to the 30-minute grid, no seconds."""
    moment = moment.replace(second=0, microsecond=0)
    moment -= timedelta(minutes=moment.minute % 30)
    if moment.hour == 23 and moment.minute == 30:
        # The last slot of the test week is 23:00 (hours end 23:30): 23:30 is not a slot.
        moment -= timedelta(minutes=30)
    return moment


def past_slot(minutes_ago=60) -> datetime:
    return grid(clock.now() - timedelta(minutes=minutes_ago))


def future_slot(minutes_ahead=90) -> datetime:
    return grid(clock.now() + timedelta(minutes=minutes_ahead + 30))


# --- pure rules -----------------------------------------------------------------------


def appt(status="agendada", marked_at=NOW - timedelta(hours=1), professional_id=1, signed_at=None, started_at=NOW - timedelta(minutes=50), **extra):
    return SimpleNamespace(
        status=status,
        marked_at=marked_at,
        professional_id=professional_id,
        signed_at=signed_at,
        signed_by_id=None,
        started_at=started_at,
        customer_id=extra.pop("customer_id", 10),
        dependent_id=extra.pop("dependent_id", None),
        **extra,
    )


def user(role="professional", professional_id=1, id=7):
    return SimpleNamespace(role=role, professional_id=professional_id, id=id)


def test_only_the_appointment_professional_can_sign():
    assert can_sign(appt(), user(), NOW)
    for who in (user("admin", None), user("supervisor", None), user("attendant", None), user("client", None), user(professional_id=2)):
        with pytest.raises(SignatureError) as refused:
            assert_can_sign(appt(), who, NOW)
        assert refused.value.status_code == 403


def test_sign_rules():
    with pytest.raises(SignatureError, match="Cancelled"):
        assert_can_sign(appt(status="cancelada"), user(), NOW)
    with pytest.raises(SignatureError, match="Already signed"):
        assert_can_sign(appt(status="realizada", signed_at=NOW), user(), NOW)
    with pytest.raises(SignatureError, match="starts at"):
        assert_can_sign(appt(marked_at=NOW + timedelta(minutes=1)), user(), NOW)
    assert can_sign(appt(marked_at=NOW), user(), NOW)  # from the start of the slot
    with pytest.raises(SignatureError, match="card code"):
        assert_can_sign(appt(started_at=None), user(), NOW)  # the patient was never verified


def test_sign_and_revoke_change_status_and_stamp():
    row = appt()
    sign(row, user(id=42), NOW.replace(second=30, microsecond=5))
    assert (row.status, row.signed_at, row.signed_by_id) == ("realizada", NOW.replace(second=30), 42)
    with pytest.raises(SignatureError, match="Already signed"):
        sign(row, user(), NOW)
    with pytest.raises(SignatureError) as other:
        assert_can_revoke(row, user(professional_id=2))
    assert other.value.status_code == 403
    revoke(row, user())
    assert (row.status, row.signed_at, row.signed_by_id) == ("agendada", None, None)
    with pytest.raises(SignatureError, match="not signed"):
        assert_can_revoke(row, user())
    sign(row, user(), NOW)
    clear_signature(row)
    assert row.signed_at is None and row.signed_by_id is None


def test_status_change_rules():
    check_status_change(None, "agendada")
    check_status_change(None, "cancelada")
    with pytest.raises(SignatureError, match=SIGNATURE_REQUIRED) as refused:
        check_status_change(None, "realizada")
    assert refused.value.status_code == 403
    with pytest.raises(SignatureError):
        check_status_change(appt(), "realizada")
    check_status_change(appt(status="realizada", signed_at=NOW), "realizada")  # already signed: a no-op


def test_signed_edit_rules():
    signed = appt(status="realizada", signed_at=NOW, customer_id=10, dependent_id=None)
    body = SimpleNamespace(status="realizada", customer_id=10, dependent_id=None, professional_id=1, marked_at=signed.marked_at)
    check_signed_edit(signed, body)  # notes only: fine
    check_signed_edit(signed, SimpleNamespace(**{**body.__dict__, "status": "cancelada"}))  # cancel: fine
    for change in ({"status": "agendada"}, {"customer_id": 11}, {"dependent_id": 3}, {"professional_id": 2}, {"marked_at": NOW}):
        with pytest.raises(SignatureError, match=SIGNED_FROZEN):
            check_signed_edit(signed, SimpleNamespace(**{**body.__dict__, **change}))
    check_signed_edit(appt(), SimpleNamespace(**{**body.__dict__, "customer_id": 11, "status": "agendada"}))  # unsigned: free
    # The kind of visit is frozen too (an omitted flag means "unchanged").
    with pytest.raises(SignatureError, match=SIGNED_FROZEN):
        check_signed_edit(signed, SimpleNamespace(**{**body.__dict__, "urgent": True}))
    check_signed_edit(signed, SimpleNamespace(**{**body.__dict__, "urgent": None}))


def test_awaiting_signature_is_scheduled_past_and_unsigned():
    assert awaiting_signature(appt(marked_at=NOW - timedelta(minutes=1)), NOW)
    assert awaiting_signature(appt(marked_at=NOW), NOW)
    assert not awaiting_signature(appt(marked_at=NOW + timedelta(minutes=1)), NOW)
    assert not awaiting_signature(appt(status="realizada", signed_at=NOW), NOW)
    assert not awaiting_signature(appt(status="cancelada"), NOW)


# --- API ------------------------------------------------------------------------------


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin)
    all_day = [{"weekday": w, "start": "00:00", "end": "23:30"} for w in range(7)]
    lorem = client.post(
        "/professionals",
        headers=admin,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "working_hours": all_day},
    ).json()["id"]
    dolor = client.post(
        "/professionals",
        headers=admin,
        json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 7000, "working_hours": all_day},
    ).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)

    attendant = auth_as(client, "attendant")

    def book(marked_at, professional=lorem, status="agendada", started=True):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": holder, "professional_id": professional, "marked_at": marked_at.isoformat(), "status": status},
        )
        assert response.status_code == 201, response.text
        row = response.json()
        if started and status == "agendada" and marked_at <= clock.now():
            # The patient was verified with the card: the precondition of every signature.
            row = start(client, row, attendant)
        return row

    c = SimpleNamespace(
        client=client,
        admin=admin,
        attendant=attendant,
        lorem=lorem,
        dolor=dolor,
        holder=holder,
        book=book,
        lorem_login=auth_as(client, "professional", professional_id=lorem),
        dolor_login=auth_as(client, "professional", professional_id=dolor),
    )
    c.client_login = auth_as(client, "client", customer_id=holder)
    return c


def test_professional_signs_own_past_appointment(ctx):
    item = ctx.book(past_slot())
    assert item["status"] == "agendada" and item["signed_at"] is None
    mine = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.lorem_login).json()
    assert mine["awaiting_signature"] is True
    assert mine["can_sign"] is True and mine["can_revoke_signature"] is False
    signed = ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login, notes="Routine visit"))
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["status"] == "realizada"
    assert body["signed_at"] is not None
    assert body["signed_by_name"] == f"Professional {ctx.lorem}"
    assert body["awaiting_signature"] is False
    assert body["can_sign"] is False and body["can_revoke_signature"] is True
    assert body["attendance"] == "completed"  # signed without the check-in flow
    assert "[signature] Routine visit" in body["notes"]
    # The patient is told the appointment is completed.
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.client_login).json()]
    assert titles[0].startswith("Appointment of") and titles[0].endswith("completed")
    # And it now pays the professional.
    today = clock.now().date()
    payout = ctx.client.get(
        f"/reports/payouts?start={(today - timedelta(days=2)).isoformat()}&end={today.isoformat()}&professional_id={ctx.lorem}",
        headers=ctx.admin,
    ).json()
    assert payout["totals"]["payout_cents"] == 5000


def test_nobody_else_can_sign(ctx):
    item = ctx.book(past_slot())
    for headers in (ctx.admin, ctx.attendant, ctx.client_login, ctx.dolor_login):
        refused = ctx.client.post(f"/appointments/{item['id']}/sign", headers=headers, json=DUMMY_SIGN_BODY)
        assert refused.status_code == 403, refused.text
    assert ctx.client.get(f"/appointments/{item['id']}", headers=ctx.admin).json()["status"] == "agendada"
    # Staff sees the flags for what they are: they cannot sign.
    staff_view = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.admin).json()
    assert staff_view["awaiting_signature"] is True and staff_view["can_sign"] is False


def test_cannot_sign_before_the_slot_cancelled_or_twice(ctx):
    future = ctx.book(future_slot())
    early = ctx.client.post(f"/appointments/{future['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert early.status_code == 409 and "starts at" in early.json()["detail"]
    assert ctx.client.get(f"/appointments/{future['id']}", headers=ctx.lorem_login).json()["can_sign"] is False

    cancelled = ctx.book(past_slot(120), status="cancelada")
    refused = ctx.client.post(f"/appointments/{cancelled['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert refused.status_code == 409 and "Cancelled" in refused.json()["detail"]

    done = ctx.book(past_slot(180))
    assert ctx.client.post(f"/appointments/{done['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 200
    twice = ctx.client.post(f"/appointments/{done['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert twice.status_code == 409 and "Already signed" in twice.json()["detail"]


def test_professional_revokes_own_signature(ctx):
    item = ctx.book(past_slot())
    ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert ctx.client.request("DELETE", f"/appointments/{item['id']}/sign", headers=ctx.dolor_login, json=DUMMY_SIGN_BODY).status_code == 403
    assert ctx.client.request("DELETE", f"/appointments/{item['id']}/sign", headers=ctx.admin, json=DUMMY_SIGN_BODY).status_code == 403
    revoked = ctx.client.request("DELETE", f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=revoke_body(ctx.client, ctx.lorem_login))
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["status"] == "agendada" and body["signed_at"] is None and body["signed_by_name"] is None
    assert body["can_sign"] is True and body["awaiting_signature"] is True
    again = ctx.client.request("DELETE", f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=revoke_body(ctx.client, ctx.lorem_login))
    assert again.status_code == 409 and "not signed" in again.json()["detail"]


def test_check_out_leaves_the_appointment_awaiting_signature(ctx):
    item = ctx.book(past_slot(30), started=False)  # inside the check-in window, slot already started
    assert ctx.client.post(f"/appointments/{item['id']}/check-in", headers=ctx.attendant).status_code == 200
    start(ctx.client, item, ctx.attendant)
    out = ctx.client.post(f"/appointments/{item['id']}/check-out", headers=ctx.attendant)
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "agendada"
    assert out.json()["attendance"] == "checked_out"
    assert out.json()["awaiting_signature"] is True
    # The professional was asked to sign; the patient got the check-out receipt, not "completed".
    asked = ctx.client.get("/notifications", headers=ctx.lorem_login).json()
    assert asked[0]["event"] == "appointment.checked_out"
    assert asked[0]["title"].startswith("Awaiting your signature: Foo Holder")
    patient = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.client_login).json()]
    assert patient[0].startswith("Check-out confirmed at")
    # Dashboard counts it for the staff and for the professional.
    assert ctx.client.get("/dashboard", headers=ctx.admin).json()["awaiting_signature"] == 1
    mine = ctx.client.get("/dashboard", headers=ctx.lorem_login).json()
    assert mine["awaiting_signature"] == 1
    assert [row["id"] for row in mine["to_sign"]] == [item["id"]]
    # Signing completes it: attendance keeps the audit trail (checked out).
    signed = ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).json()
    assert signed["status"] == "realizada" and signed["attendance"] == "checked_out"
    assert ctx.client.get("/dashboard", headers=ctx.admin).json()["awaiting_signature"] == 0
    assert ctx.client.get("/dashboard", headers=ctx.lorem_login).json()["to_sign"] == []


def test_staff_may_cancel_a_signed_appointment_but_not_change_it(ctx):
    item = ctx.book(past_slot())
    ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    base = {"customer_id": ctx.holder, "professional_id": ctx.lorem, "marked_at": item["marked_at"]}
    frozen = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "status": "agendada"})
    assert frozen.status_code == 409 and "Signed" in frozen.json()["detail"]
    moved = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "professional_id": ctx.dolor, "status": "realizada"})
    assert moved.status_code == 409
    noted = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "status": "realizada", "notes": "record"})
    assert noted.status_code == 200 and noted.json()["signed_at"] is not None
    cancelled = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "status": "cancelada"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelada" and cancelled.json()["signed_at"] is None
    # Re-activating it leaves it unsigned again.
    back = ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "status": "agendada"})
    assert back.status_code == 200 and back.json()["status"] == "agendada" and back.json()["awaiting_signature"] is True


def test_professionals_do_not_book_or_edit(ctx):
    item = ctx.book(past_slot())
    payload = {"customer_id": ctx.holder, "professional_id": ctx.lorem, "marked_at": future_slot().isoformat()}
    assert ctx.client.post("/appointments", headers=ctx.lorem_login, json=payload).status_code == 403
    assert ctx.client.put(f"/appointments/{item['id']}", headers=ctx.lorem_login, json={**payload, "status": "cancelada"}).status_code == 403
    assert ctx.client.delete(f"/appointments/{item['id']}", headers=ctx.lorem_login).status_code == 403

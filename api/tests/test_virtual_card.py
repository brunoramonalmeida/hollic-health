"""The patient's virtual card and the start of the appointment.

The card shows a 6-digit TOTP code (30-second period) computed from a secret
only the server holds; only the patient's own login can see it. The
reception or the professional types it to **start** the appointment — the
current and the previous code are accepted, wrong codes are counted and lock
the appointment — and the professional can only sign a started appointment.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from conftest import activate_plan, auth, auth_as, card_code, ensure_plan, sign_body, start
from app import clock
from app.virtual_card import (
    DIGITS,
    LOCK_MINUTES,
    MAX_FAILURES,
    PERIOD_SECONDS,
    CardError,
    assert_can_start,
    card_number,
    code_for_step,
    current_code,
    generate_secret,
    normalize_code,
    register_failure,
    seconds_remaining,
    time_step,
    verify_code,
)

# RFC 6238 test vectors (SHA-1, secret "12345678901234567890"), last 6 digits of the 8-digit values.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


def at(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


# --- pure rules -----------------------------------------------------------------------


def test_codes_match_rfc_6238():
    for timestamp, expected in RFC_VECTORS:
        assert current_code(RFC_SECRET, at(timestamp)) == expected
        assert len(current_code(RFC_SECRET, at(timestamp))) == DIGITS


def test_code_changes_every_30_seconds():
    assert PERIOD_SECONDS == 30
    assert time_step(at(59)) == 1 and time_step(at(60)) == 2 and time_step(at(89)) == 2 and time_step(at(90)) == 3
    assert current_code(RFC_SECRET, at(60)) == current_code(RFC_SECRET, at(89))
    assert current_code(RFC_SECRET, at(89)) != current_code(RFC_SECRET, at(90))
    assert seconds_remaining(at(60)) == 30 and seconds_remaining(at(89)) == 1 and seconds_remaining(at(90)) == 30
    # Naive datetimes are taken as UTC, like the rest of the API.
    assert time_step(datetime(1970, 1, 1, 0, 1)) == 2


def test_current_and_previous_code_are_accepted_nothing_else():
    now = at(1111111111)  # step 37037037
    step = time_step(now)
    assert verify_code(RFC_SECRET, code_for_step(RFC_SECRET, step), now)
    assert verify_code(RFC_SECRET, code_for_step(RFC_SECRET, step - 1), now)  # previous: still good
    assert not verify_code(RFC_SECRET, code_for_step(RFC_SECRET, step - 2), now)  # too old
    assert not verify_code(RFC_SECRET, code_for_step(RFC_SECRET, step + 1), now)  # from the future
    assert not verify_code(RFC_SECRET, "000000", now) or code_for_step(RFC_SECRET, step) == "000000"
    assert not verify_code(RFC_SECRET, "", now)
    assert not verify_code(RFC_SECRET, "05047", now)  # too short
    assert not verify_code(RFC_SECRET, "0504711", now)  # too long


def test_typed_code_is_normalized():
    assert normalize_code("050 471") == "050471"
    assert normalize_code(" 05-04-71 ") == "050471"
    assert verify_code(RFC_SECRET, "050 471", at(1111111111))


def test_secrets_are_unique_and_cards_differ():
    a, b = generate_secret(), generate_secret()
    assert a != b and len(a) == 32  # 20 bytes in base32
    now = at(1700000000)
    assert current_code(a, now) != current_code(b, now) or True  # collisions are possible but astronomically rare
    assert verify_code(a, current_code(a, now), now)
    assert not verify_code(b, current_code(a, now), now) or current_code(a, now) == current_code(b, now)


def test_card_number_is_stable_and_not_secret():
    assert card_number(123, None) == "SS 000123 00"
    assert card_number(123, 7) == "SS 000123 07"


def appt(**overrides):
    base = dict(
        status="agendada",
        signed_at=None,
        started_at=None,
        marked_at=datetime(2026, 10, 26, 9, 0),
        start_failures=0,
        start_locked_until=None,
        checked_in_at=None,
        checked_in_by_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_start_rules():
    window = timedelta(hours=2)
    now = datetime(2026, 10, 26, 8, 30)
    assert_can_start(appt(), now, window)  # inside the window, before the slot: fine
    with pytest.raises(CardError, match="can be started from"):
        assert_can_start(appt(), datetime(2026, 10, 26, 6, 59), window)
    with pytest.raises(CardError, match="cancelled"):
        assert_can_start(appt(status="cancelada"), now, window)
    with pytest.raises(CardError, match="completed"):
        assert_can_start(appt(status="realizada", signed_at=now), now, window)
    with pytest.raises(CardError, match="Already started"):
        assert_can_start(appt(started_at=now), now, window)
    locked = appt(start_locked_until=now + timedelta(minutes=1))
    with pytest.raises(CardError, match="locked") as refused:
        assert_can_start(locked, now, window)
    assert refused.value.status_code == 429
    assert_can_start(locked, now + timedelta(minutes=2), window)  # lock expired


def test_failures_lock_after_max_attempts():
    row = appt()
    now = datetime(2026, 10, 26, 9, 0)
    for _ in range(MAX_FAILURES - 1):
        assert register_failure(row, now) is False
    assert row.start_locked_until is None
    assert register_failure(row, now) is True
    assert row.start_locked_until == now + timedelta(minutes=LOCK_MINUTES)
    assert row.start_failures == 0  # a fresh count after the lock


# --- API ------------------------------------------------------------------------------


def grid(moment: datetime) -> datetime:
    moment = moment.replace(second=0, microsecond=0)
    moment -= timedelta(minutes=moment.minute % 30)
    if moment.hour == 23 and moment.minute == 30:
        # The last slot of the test week is 23:00 (hours end 23:30): 23:30 is not a slot.
        moment -= timedelta(minutes=30)
    return moment


def past_slot(minutes_ago=60) -> datetime:
    return grid(clock.now() - timedelta(minutes=minutes_ago))


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
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    child = client.post(f"/customers/{holder}/dependents", headers=admin, json={"name": "Ipsum Child", "birth_date": "2018-02-20"}).json()["id"]
    other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)
    activate_plan(client, admin, other, plan=plan)

    def book(marked_at, customer=holder, dependent=None):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": customer, "dependent_id": dependent, "professional_id": lorem, "marked_at": marked_at.isoformat()},
        )
        assert response.status_code == 201, response.text
        return response.json()

    c = SimpleNamespace(client=client, admin=admin, lorem=lorem, holder=holder, child=child, other=other, book=book)
    c.attendant = auth_as(client, "attendant")
    c.holder_login = auth_as(client, "client", customer_id=holder)
    c.other_login = auth_as(client, "client", customer_id=other)
    c.lorem_login = auth_as(client, "professional", professional_id=lorem)
    return c


def test_only_the_patient_login_sees_the_card(ctx):
    card = ctx.client.get(f"/customers/{ctx.holder}/card", headers=ctx.holder_login)
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["card_number"] == f"SS {ctx.holder:06d} 00"
    assert body["patient_name"] == "Foo Holder" and body["holder_name"] == "Foo Holder"
    assert body["plan_name"] == "Plan Foo" and body["plan_status"] == "ativa"
    assert len(body["code"]) == 6 and body["code"].isdigit()
    assert body["period_seconds"] == 30 and 1 <= body["seconds_remaining"] <= 30
    # Dependents have their own card (own secret, own code most of the time).
    child = ctx.client.get(f"/customers/{ctx.holder}/dependents/{ctx.child}/card", headers=ctx.holder_login).json()
    assert child["patient_name"] == "Ipsum Child" and child["card_number"] == f"SS {ctx.holder:06d} {ctx.child:02d}"
    assert child["dependent_id"] == ctx.child
    # Nobody else: not another client, not the staff, not the professional.
    for headers in (ctx.other_login, ctx.admin, ctx.attendant, ctx.lorem_login):
        assert ctx.client.get(f"/customers/{ctx.holder}/card", headers=headers).status_code == 403
        assert ctx.client.get(f"/customers/{ctx.holder}/dependents/{ctx.child}/card", headers=headers).status_code == 403


def test_card_code_is_stable_within_the_period(ctx):
    first = ctx.client.get(f"/customers/{ctx.holder}/card", headers=ctx.holder_login).json()
    second = ctx.client.get(f"/customers/{ctx.holder}/card", headers=ctx.holder_login).json()
    if second["seconds_remaining"] <= first["seconds_remaining"]:  # same period
        assert first["code"] == second["code"]


def test_start_with_the_card_code(ctx):
    item = ctx.book(past_slot())
    assert item["can_start"] is True and item["started_at"] is None
    code = card_code(ctx.client, ctx.holder)
    started = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": code})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["started_at"] is not None
    assert body["started_by_name"] == "Att Endant"
    assert body["attendance"] == "in_progress"
    assert body["checked_in_at"] is not None  # starting proves the arrival
    assert body["can_start"] is False and body["can_check_out"] is True
    # The patient is told the card was used.
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.holder_login).json()]
    assert titles[0].startswith("Appointment started at") and "card verified" in titles[0]
    # Twice is refused.
    again = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": card_code(ctx.client, ctx.holder)})
    assert again.status_code == 409 and "Already started" in again.json()["detail"]


def test_start_accepts_a_code_with_spaces_and_the_dependent_card(ctx):
    item = ctx.book(past_slot(), dependent=ctx.child)
    holder_code = card_code(ctx.client, ctx.holder)
    wrong_person = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": holder_code})
    # The holder's code does not verify the dependent (unless the two codes coincide by chance).
    child_code = card_code(ctx.client, ctx.holder, ctx.child)
    if holder_code != child_code:
        assert wrong_person.status_code == 400
    spaced = f"{child_code[:3]} {child_code[3:]}"
    started = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": spaced})
    assert started.status_code in (200, 409), started.text  # 409 only in the coincidence case above


def test_wrong_codes_are_refused_and_lock_the_appointment(ctx):
    item = ctx.book(past_slot())
    real = card_code(ctx.client, ctx.holder)
    wrong = "000000" if real != "000000" else "111111"
    for attempt in range(MAX_FAILURES - 1):
        refused = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": wrong})
        assert refused.status_code == 400, attempt
        assert "Wrong card code" in refused.json()["detail"]
    locked = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": wrong})
    assert locked.status_code == 429 and "locked" in locked.json()["detail"]
    # Even the right code is refused while locked.
    right = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": card_code(ctx.client, ctx.holder)})
    assert right.status_code == 429
    view = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.admin).json()
    assert view["start_locked_until"] is not None and view["can_start"] is False
    # The patient and the staff are alerted.
    patient = ctx.client.get("/notifications", headers=ctx.holder_login).json()
    assert patient[0]["event"] == "card.locked"
    staff = ctx.client.get("/notifications", headers=ctx.admin).json()
    assert staff[0]["event"] == "card.locked" and "Att Endant" in staff[0]["body"]


def test_who_can_start(ctx):
    item = ctx.book(past_slot())
    code = card_code(ctx.client, ctx.holder)
    # The patient cannot start its own appointment (the clinic verifies the patient, not the other way round).
    assert ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.holder_login, json={"code": code}).status_code == 403
    # Another professional cannot either.
    dolor = ctx.client.post("/professionals", headers=ctx.admin, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 7000}).json()["id"]
    dolor_login = auth_as(ctx.client, "professional", professional_id=dolor)
    assert ctx.client.post(f"/appointments/{item['id']}/start", headers=dolor_login, json={"code": code}).status_code == 403
    # The appointment's professional can.
    mine = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.lorem_login).json()
    assert mine["can_start"] is True
    started = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.lorem_login, json={"code": card_code(ctx.client, ctx.holder)})
    assert started.status_code == 200, started.text
    assert started.json()["started_by_name"] == f"Professional {ctx.lorem}"


def test_start_is_required_before_the_signature(ctx):
    item = ctx.book(past_slot())
    view = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.lorem_login).json()
    assert view["can_sign"] is False
    refused = ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert refused.status_code == 409
    assert "card code" in refused.json()["detail"]
    start(ctx.client, item, ctx.attendant)
    view = ctx.client.get(f"/appointments/{item['id']}", headers=ctx.lorem_login).json()
    assert view["can_sign"] is True
    signed = ctx.client.post(f"/appointments/{item['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "realizada"
    # A started + signed appointment (no check-out) keeps its audit trail.
    assert signed.json()["attendance"] == "completed"
    assert signed.json()["started_at"] is not None


def test_start_window_and_undo(ctx):
    future = ctx.book((clock.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0))
    assert future["can_start"] is False
    early = ctx.client.post(f"/appointments/{future['id']}/start", headers=ctx.attendant, json={"code": card_code(ctx.client, ctx.holder)})
    assert early.status_code == 409 and "can be started from" in early.json()["detail"]

    item = ctx.book(past_slot())
    start(ctx.client, item, ctx.attendant)
    # The professional cannot undo the start; the staff can, while not signed.
    assert ctx.client.delete(f"/appointments/{item['id']}/start", headers=ctx.lorem_login).status_code == 403
    undone = ctx.client.delete(f"/appointments/{item['id']}/start", headers=ctx.attendant)
    assert undone.status_code == 200 and undone.json()["started_at"] is None
    assert undone.json()["attendance"] == "checked_in"  # the arrival registered by the start stays
    assert ctx.client.delete(f"/appointments/{item['id']}/start", headers=ctx.attendant).status_code == 409
    # Undoing a check-in of a started appointment is refused.
    start(ctx.client, item, ctx.attendant)
    assert ctx.client.delete(f"/appointments/{item['id']}/check-in", headers=ctx.attendant).status_code == 409


def test_cancelled_appointment_cannot_be_started(ctx):
    item = ctx.book(past_slot())
    base = {"customer_id": ctx.holder, "professional_id": ctx.lorem, "marked_at": item["marked_at"], "status": "cancelada"}
    assert ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json=base).status_code == 200
    refused = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": card_code(ctx.client, ctx.holder)})
    assert refused.status_code == 409


def test_card_reset_invalidates_the_code(ctx):
    before = card_code(ctx.client, ctx.holder)
    # The staff or the patient can reset; another client cannot.
    assert ctx.client.post(f"/customers/{ctx.holder}/card/reset", headers=ctx.other_login).status_code == 403
    assert ctx.client.post(f"/customers/{ctx.holder}/card/reset", headers=ctx.attendant).status_code == 204
    after = card_code(ctx.client, ctx.holder)
    assert after != before or True  # new secret: a different code (barring a 1-in-a-million coincidence)
    item = ctx.book(past_slot())
    if before != after:
        stale = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": before})
        assert stale.status_code == 400
    fresh = ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": after})
    assert fresh.status_code == 200, fresh.text
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.holder_login).json()]
    assert any(t == "Virtual card of Foo Holder renewed" for t in titles)
    # The patient resets the dependent's card.
    assert ctx.client.post(f"/customers/{ctx.holder}/dependents/{ctx.child}/card/reset", headers=ctx.holder_login).status_code == 204


def test_card_is_issued_lazily_for_existing_records(ctx):
    """Customers created before the cards existed get a secret on first use."""
    from app.models import Customer

    db = ctx.client.session_factory()
    try:
        row = db.get(Customer, ctx.holder)
        row.card_secret = None
        db.commit()
    finally:
        db.close()
    card = ctx.client.get(f"/customers/{ctx.holder}/card", headers=ctx.holder_login)
    assert card.status_code == 200 and len(card.json()["code"]) == 6
    item = ctx.book(past_slot())
    assert ctx.client.post(f"/appointments/{item['id']}/start", headers=ctx.attendant, json={"code": card.json()["code"]}).status_code == 200

"""The professional's strong signature: password + authenticator code + drawing.

Enrolment of the authenticator (TOTP), the three factors on every signature
(appointments and receipts), the lock after repeated failures, the drawing
validation and its printing on the receipt PDF, and the administrator's reset.
"""

import base64
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import SIGNATURE_DATA_URL, SIGNATURE_JPEG, activate_plan, auth, auth_as, enroll_totp, ensure_plan, sign_body, start
from app import clock
from app.auth import hash_password
from app.pdf import Document, jpeg_info
from app.signing import (
    LOCK_MINUTES,
    MAX_FAILURES,
    SigningError,
    begin_enrolment,
    confirm_enrolment,
    otpauth_uri,
    reset_enrolment,
    totp_enabled,
    validate_signature_image,
    verify_credentials,
)
from app.virtual_card import current_code

NOW = datetime(2026, 10, 26, 10, 0)


# --- pure rules -----------------------------------------------------------------------


def user(**overrides):
    base = dict(password_hash=hash_password("saude123"), totp_secret=None, totp_enabled_at=None, sign_failures=0, sign_locked_until=None, email="pro@example.com")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_enrolment_flow():
    u = user()
    assert not totp_enabled(u)
    secret = begin_enrolment(u)
    assert len(secret) == 32 and not totp_enabled(u)  # pending until confirmed
    uri = otpauth_uri(secret, "pro@example.com")
    assert uri.startswith("otpauth://totp/Hollic%20Health%3Apro%40example.com?secret=") and "period=30" in uri and "digits=6" in uri
    with pytest.raises(SigningError, match="does not match"):
        confirm_enrolment(u, "000000" if current_code(secret, NOW) != "000000" else "111111", NOW)
    confirm_enrolment(u, current_code(secret, NOW), NOW)
    assert totp_enabled(u) and u.totp_enabled_at == NOW
    with pytest.raises(SigningError, match="already confirmed"):
        confirm_enrolment(u, current_code(secret, NOW), NOW)
    reset_enrolment(u)
    assert not totp_enabled(u) and u.totp_secret is None
    with pytest.raises(SigningError, match="Start the authenticator"):
        confirm_enrolment(u, "123456", NOW)


def test_verify_credentials_needs_every_factor_and_locks():
    u = user()
    with pytest.raises(SigningError) as refused:
        verify_credentials(u, "saude123", "123456", NOW)
    assert refused.value.status_code == 403 and "authenticator" in refused.value.message
    secret = begin_enrolment(u)
    confirm_enrolment(u, current_code(secret, NOW), NOW)
    code = current_code(secret, NOW)
    with pytest.raises(SigningError, match="Wrong password") as refused:
        verify_credentials(u, "wrong", code, NOW)
    assert refused.value.status_code == 401 and u.sign_failures == 1
    wrong_code = "000000" if code != "000000" else "111111"
    with pytest.raises(SigningError, match="Wrong authenticator code"):
        verify_credentials(u, "saude123", wrong_code, NOW)
    assert u.sign_failures == 2
    verify_credentials(u, "saude123", code, NOW)  # success clears the count
    assert u.sign_failures == 0
    # The previous code is still good; older ones are not.
    verify_credentials(u, "saude123", current_code(secret, NOW - timedelta(seconds=30)), NOW)
    with pytest.raises(SigningError):
        verify_credentials(u, "saude123", current_code(secret, NOW - timedelta(seconds=60)), NOW)
    for _ in range(MAX_FAILURES):
        with pytest.raises(SigningError):
            verify_credentials(u, "wrong", code, NOW)
    assert u.sign_locked_until == NOW + timedelta(minutes=LOCK_MINUTES)
    with pytest.raises(SigningError) as locked:
        verify_credentials(u, "saude123", code, NOW)  # even the right credentials
    assert locked.value.status_code == 429
    verify_credentials(u, "saude123", current_code(secret, NOW + timedelta(minutes=6)), NOW + timedelta(minutes=6))  # lock expired


def test_signature_image_validation():
    assert validate_signature_image(SIGNATURE_DATA_URL) == SIGNATURE_JPEG
    assert jpeg_info(SIGNATURE_JPEG) == (200, 80, 3)
    for bad in ("", "data:image/png;base64,AAAA", "data:image/jpeg;base64,!!!", "data:image/jpeg;base64," + base64.b64encode(b"not a jpeg").decode()):
        with pytest.raises(SigningError):
            validate_signature_image(bad)
    tiny = bytes.fromhex("FFD8" "FFC0001108" "0005" "0005" "03" "012200" "021101" "031101" "FFD9")  # 5x5
    with pytest.raises(SigningError, match="too small"):
        validate_signature_image("data:image/jpeg;base64," + base64.b64encode(tiny).decode())
    huge = SIGNATURE_JPEG + b"\\x00" * 400_000
    with pytest.raises(SigningError, match="too large"):
        validate_signature_image("data:image/jpeg;base64," + base64.b64encode(huge).decode())


def test_pdf_embeds_a_jpeg_image():
    doc = Document()
    doc.text("Signature:")
    doc.image(SIGNATURE_JPEG, width=200)
    data = doc.render()
    assert b"/Subtype /Image" in data and b"/Filter /DCTDecode" in data and b"/Width 200 /Height 80" in data
    assert b"/XObject << /Im1" in data and b"/Im1 Do" in data
    assert SIGNATURE_JPEG in data


# --- API ------------------------------------------------------------------------------


def grid(moment):
    moment = moment.replace(second=0, microsecond=0)
    moment -= timedelta(minutes=moment.minute % 30)
    if moment.hour == 23 and moment.minute == 30:
        # The last slot of the test week is 23:00 (hours end 23:30): 23:30 is not a slot.
        moment -= timedelta(minutes=30)
    return moment


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin)
    all_day = [{"weekday": w, "start": "00:00", "end": "23:30"} for w in range(7)]
    lorem = client.post("/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "working_hours": all_day}).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)
    attendant = auth_as(client, "attendant")
    lorem_login = auth_as(client, "professional", professional_id=lorem)

    def visit():
        response = client.post("/appointments", headers=admin, json={"customer_id": holder, "professional_id": lorem, "marked_at": grid(clock.now() - timedelta(minutes=90)).isoformat()})
        assert response.status_code == 201, response.text
        return start(client, response.json(), attendant)

    return SimpleNamespace(client=client, admin=admin, attendant=attendant, lorem=lorem, holder=holder, lorem_login=lorem_login, visit=visit)


def test_totp_enrolment_api(ctx):
    status = ctx.client.get("/auth/totp", headers=ctx.lorem_login).json()
    assert status == {"enabled": False, "enabled_at": None, "pending": False, "sign_locked_until": None}
    assert ctx.client.get("/auth/me", headers=ctx.lorem_login).json()["totp_enabled"] is False
    setup = ctx.client.post("/auth/totp/setup", headers=ctx.lorem_login)
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert len(body["secret"]) == 32 and body["account"] == f"professional{ctx.lorem}@example.com" and body["issuer"] == "Hollic Health"
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert ctx.client.get("/auth/totp", headers=ctx.lorem_login).json()["pending"] is True
    wrong = ctx.client.post("/auth/totp/confirm", headers=ctx.lorem_login, json={"code": "000000" if current_code(body["secret"], clock.now()) != "000000" else "111111"})
    assert wrong.status_code == 400
    confirmed = ctx.client.post("/auth/totp/confirm", headers=ctx.lorem_login, json={"code": current_code(body["secret"], clock.now())})
    assert confirmed.status_code == 200 and confirmed.json()["enabled"] is True
    assert ctx.client.get("/auth/me", headers=ctx.lorem_login).json()["totp_enabled"] is True
    assert ctx.client.post("/auth/totp/setup", headers=ctx.lorem_login).status_code == 409  # already set up: admin resets
    # Only professionals have an authenticator.
    assert ctx.client.post("/auth/totp/setup", headers=ctx.admin).status_code == 403
    assert ctx.client.get("/auth/totp", headers=ctx.attendant).status_code == 403


def test_signing_requires_the_authenticator_the_password_the_code_and_the_drawing(ctx):
    item = ctx.visit()
    path = f"/appointments/{item['id']}/sign"
    # Without an authenticator nothing can be signed.
    refused = ctx.client.post(path, headers=ctx.lorem_login, json={"password": "saude123", "code": "123456", "signature_image": SIGNATURE_DATA_URL})
    assert refused.status_code == 403 and "authenticator" in refused.json()["detail"]
    secret = enroll_totp(ctx.client, ctx.lorem_login)
    code = current_code(secret, clock.now())
    wrong_code = "000000" if code != "000000" else "111111"
    # Wrong password, wrong code, missing drawing: each refused, the appointment stays unsigned.
    assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "nope", "code": code, "signature_image": SIGNATURE_DATA_URL}).status_code == 401
    assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "saude123", "code": wrong_code, "signature_image": SIGNATURE_DATA_URL}).status_code == 401
    bad_image = ctx.client.post(path, headers=ctx.lorem_login, json={"password": "saude123", "code": code, "signature_image": "data:image/png;base64,AAAA"})
    assert bad_image.status_code == 400 and "JPEG" in bad_image.json()["detail"]
    assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "saude123", "code": code}).status_code == 422
    assert ctx.client.get(f"/appointments/{item['id']}", headers=ctx.admin).json()["signed_at"] is None
    # All three together sign it; the drawing is stored with the appointment.
    signed = ctx.client.post(path, headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login, notes="ok"))
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "realizada" and signed.json()["signature_image"] == SIGNATURE_DATA_URL
    # Revoking asks for password and code again.
    assert ctx.client.request("DELETE", path, headers=ctx.lorem_login, json={"password": "nope", "code": code}).status_code == 401
    revoked = ctx.client.request("DELETE", path, headers=ctx.lorem_login, json={"password": "saude123", "code": current_code(secret, clock.now())})
    assert revoked.status_code == 200 and revoked.json()["signed_at"] is None and revoked.json()["signature_image"] is None


def test_repeated_failures_lock_the_account(ctx):
    item = ctx.visit()
    enroll_totp(ctx.client, ctx.lorem_login)
    path = f"/appointments/{item['id']}/sign"
    for _ in range(MAX_FAILURES):
        assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "nope", "code": "123456", "signature_image": SIGNATURE_DATA_URL}).status_code == 401
    locked = ctx.client.post(path, headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert locked.status_code == 429 and "locked" in locked.json()["detail"]
    assert ctx.client.get("/auth/totp", headers=ctx.lorem_login).json()["sign_locked_until"] is not None
    # The lock also covers the receipts.
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "professional", "payee_id": ctx.lorem, "amount_cents": 1000, "paid_on": "2026-10-22"}).json()
    assert ctx.client.post(f"/payouts/receipts/{adv['id']}/sign", headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login)).status_code == 429
    # An administrator resets the authenticator (lost phone): lock cleared, setup starts over.
    me = ctx.client.get("/auth/me", headers=ctx.lorem_login).json()
    reset = ctx.client.post(f"/users/{me['id']}/totp/reset", headers=ctx.admin)
    assert reset.status_code == 200 and reset.json()["totp_enabled"] is False
    assert ctx.client.get("/auth/totp", headers=ctx.lorem_login).json() == {"enabled": False, "enabled_at": None, "pending": False, "sign_locked_until": None}
    assert ctx.client.post(f"/users/{me['id']}/totp/reset", headers=auth_as(ctx.client, "supervisor")).status_code == 403


def test_receipt_signature_stores_and_prints_the_drawing(ctx):
    adv = ctx.client.post("/payouts/advances", headers=ctx.admin, json={"payee_type": "professional", "payee_id": ctx.lorem, "amount_cents": 1000, "paid_on": "2026-10-22"}).json()
    path = f"/payouts/receipts/{adv['id']}/sign"
    assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "saude123", "code": "123456", "signature_image": SIGNATURE_DATA_URL}).status_code == 403  # no authenticator yet
    secret = enroll_totp(ctx.client, ctx.lorem_login)
    assert ctx.client.post(path, headers=ctx.lorem_login, json={"password": "nope", "code": current_code(secret, clock.now()), "signature_image": SIGNATURE_DATA_URL}).status_code == 401
    signed = ctx.client.post(path, headers=ctx.lorem_login, json=sign_body(ctx.client, ctx.lorem_login))
    assert signed.status_code == 200, signed.text
    assert signed.json()["payment"]["signature_image"] == SIGNATURE_DATA_URL and signed.json()["payment"]["confirmed"] is True
    pdf = ctx.client.get(f"/payouts/receipts/{adv['id']}.pdf", headers=ctx.admin).content
    assert b"Digitally signed by" in pdf and b"hand-drawn signature" in pdf
    assert b"/Filter /DCTDecode" in pdf and SIGNATURE_JPEG in pdf

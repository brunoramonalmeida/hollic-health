import os
import tempfile
from datetime import datetime

# The suite must behave the same on any machine: whatever a local ``.env``
# says, the tests run on an in-memory database, the in-process broker and a
# memory mail sender, with the default settings of every tunable. The
# in-process worker loop stays off; the tests drive the worker explicitly.
TEST_ENV = {
    "DATABASE_URL": "sqlite://",
    "BROKER_URL": "memory",
    "MAIL_BACKEND": "memory",
    "WORKER_ENABLED": "0",
    "WORKER_INTERVAL_SECONDS": "30",
    "SECRET_KEY": "test-secret",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "10080",
    "SETTLEMENT_DAY": "1",
    "SETTLEMENT_FIRST_DUE_DAYS": "5",
    "SETTLEMENT_INTERVAL_DAYS": "30",
    "SETTLEMENT_INSTALLMENT_STEP_CENTS": "200000",
    "SETTLEMENT_MAX_INSTALLMENTS": "3",
    "CHECKIN_OPENS_BEFORE_MINUTES": "120",
    "CHECKIN_CLOSES_AFTER_MINUTES": "120",
    # Prescription files go to a throw-away directory of this run.
    "PRESCRIPTIONS_DIR": tempfile.mkdtemp(prefix="sante-prescriptions-"),
    "PRESCRIPTION_MAX_BYTES": "10485760",
    "CHAT_ATTACHMENTS_DIR": tempfile.mkdtemp(prefix="sante-chat-"),
    "CHAT_ATTACHMENT_MAX_BYTES": "10485760",
    "PRESCRIPTION_LINK_DAYS": "30",
    # The trust bundle of the test PKI (written by tests/pki.py on first use).
    "ICP_BRASIL_TRUST_BUNDLE": os.path.join(tempfile.mkdtemp(prefix="sante-pki-"), "roots.pem"),
    "ICP_BRASIL_POLICY_OID": "",
    "ICP_BRASIL_POLICY_URL": "",
    "ICP_BRASIL_POLICY_SHA256": "",
}
os.environ.update(TEST_ENV)

import pytest

from app import clock

# Every test starts at this instant (a Thursday morning, inside the plans
# and fixed dates used across the suite) and the clock ticks from there, so
# the outcome never depends on the date, the time of day or the timezone of
# the machine. Tests that need "today" or "now" read the application clock.
ANCHOR = datetime(2026, 10, 22, 10, 0, 0)
clock.travel_to(ANCHOR)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.broker import MemoryBroker, set_broker
from app.mailer import MemorySender, set_sender
from app.main import app
from app.models import Clinic, Specialty, User


# PBKDF2 is deliberately slow; hash the shared test password once per session.
PASSWORD_HASH = hash_password("saude123")


@pytest.fixture(autouse=True)
def anchored_clock():
    """Each test starts exactly at ANCHOR (see above)."""
    clock.travel_to(ANCHOR)
    yield
    clock.travel_to(ANCHOR)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    password = PASSWORD_HASH
    db.add_all(
        [
            User(name="Foo Bar", email="foo.bar@example.com", password_hash=password, role="admin"),
            User(name="Sup Visor", email="supervisor@example.com", password_hash=password, role="supervisor"),
            User(name="Att Endant", email="attendant@example.com", password_hash=password, role="attendant"),
        ]
    )
    clinic = Clinic(name="Clinic Foo", city="Sample", fee_cents=2000)
    specialty = Specialty(name="Clínico geral", price_cents=8000)
    db.add_all([clinic, specialty])
    db.commit()
    db.close()

    def override_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    set_sender(MemorySender())
    set_broker(MemoryBroker())  # a fresh in-process broker per test
    with TestClient(app) as test_client:
        test_client.session_factory = TestingSession
        yield test_client
    app.dependency_overrides.clear()
    set_sender(None)
    set_broker(None)


@pytest.fixture()
def outbox():
    """The memory email sender used by the current test."""
    from app.mailer import get_sender

    return get_sender()


def auth(client, email="foo.bar@example.com", password="saude123"):
    """Bearer headers; the default account is the admin."""
    response = client.post("/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def auth_as(client, role, customer_id=None, professional_id=None, seller_id=None):
    """Headers for a user of the given role. Client users are created on the
    fly (by the admin) and linked to ``customer_id``; professional users
    likewise, linked to ``professional_id``; seller users to ``seller_id``."""
    if role == "admin":
        return auth(client)
    if role == "supervisor":
        return auth(client, "supervisor@example.com")
    if role == "attendant":
        return auth(client, "attendant@example.com")
    if role == "professional":
        assert professional_id is not None
        email = f"professional{professional_id}@example.com"
        created = client.post(
            "/users",
            headers=auth(client),
            json={
                "name": f"Professional {professional_id}",
                "email": email,
                "role": "professional",
                "password": "saude123",
                "professional_id": professional_id,
            },
        )
        assert created.status_code in (201, 409), created.text
        return auth(client, email)
    if role == "seller":
        assert seller_id is not None
        email = f"seller{seller_id}@example.com"
        created = client.post(
            "/users",
            headers=auth(client),
            json={"name": f"Seller {seller_id}", "email": email, "role": "seller", "password": "saude123", "seller_id": seller_id},
        )
        assert created.status_code in (201, 409), created.text
        return auth(client, email)
    assert role == "client" and customer_id is not None
    email = f"client{customer_id}@example.com"
    created = client.post(
        "/users",
        headers=auth(client),
        json={"name": f"Client {customer_id}", "email": email, "role": "client", "password": "saude123", "customer_id": customer_id},
    )
    assert created.status_code in (201, 409), created.text
    return auth(client, email)


def card_code(client, customer_id, dependent_id=None):
    """The code on the patient's virtual card right now (as the patient's login sees it)."""
    headers = auth_as(client, "client", customer_id=customer_id)
    path = f"/customers/{customer_id}/card" if dependent_id is None else f"/customers/{customer_id}/dependents/{dependent_id}/card"
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["code"]


def start(client, appointment, headers):
    """Start an appointment through the API with the patient's current card code."""
    code = card_code(client, appointment["customer_id"], appointment.get("dependent_id"))
    response = client.post(f"/appointments/{appointment['id']}/start", headers=headers, json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()


# A minimal JPEG (200x80, 3 components): enough for the signature-pad validation.
SIGNATURE_JPEG = bytes.fromhex("FFD8" "FFC0001108" "0050" "00C8" "03" "012200" "021101" "031101" "FFD9")
SIGNATURE_DATA_URL = "data:image/jpeg;base64," + __import__("base64").b64encode(SIGNATURE_JPEG).decode()


def enroll_totp(client, headers):
    """Set up the authenticator of the professional behind ``headers`` (once) and return its secret."""
    secrets = getattr(client, "totp_secrets", None)
    if secrets is None:
        secrets = client.totp_secrets = {}
    key = headers["Authorization"]
    if key in secrets:
        return secrets[key]
    from app.virtual_card import current_code

    status = client.get("/auth/totp", headers=headers).json()
    if status["enabled"]:
        raise RuntimeError("authenticator already enabled outside the helper")
    setup = client.post("/auth/totp/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirmed = client.post("/auth/totp/confirm", headers=headers, json={"code": current_code(secret, clock.now())})
    assert confirmed.status_code == 200, confirmed.text
    secrets[key] = secret
    return secret


def sign_body(client, headers, notes="", password="saude123"):
    """Everything a professional's signature needs: password, authenticator code, drawing."""
    from app.virtual_card import current_code

    secret = enroll_totp(client, headers)
    return {"password": password, "code": current_code(secret, clock.now()), "signature_image": SIGNATURE_DATA_URL, "notes": notes}


DUMMY_SIGN_BODY = {"password": "x", "code": "000000", "signature_image": SIGNATURE_DATA_URL}


def revoke_body(client, headers, password="saude123"):
    from app.virtual_card import current_code

    secret = enroll_totp(client, headers)
    return {"password": password, "code": current_code(secret, clock.now())}


def sign(client, appointment_id, when=None):
    """Complete an appointment the only way the system allows: with the
    signature of its professional.

    The API refuses a signature before the slot starts, and most tests book
    fixed dates that may be in the future, so this writes the signature
    straight to the database (the endpoint itself is covered by
    ``test_signature.py``). Returns the professional login's headers."""
    from app.models import Appointment, User
    from app.notifications import appointment_signed
    from app.signature import STATUS_COMPLETED

    db = client.session_factory()
    try:
        row = db.get(Appointment, appointment_id)
        assert row is not None
        professional_id = row.professional_id
    finally:
        db.close()
    headers = auth_as(client, "professional", professional_id=professional_id)
    db = client.session_factory()
    try:
        row = db.get(Appointment, appointment_id)
        user = db.query(User).filter(User.email == f"professional{professional_id}@example.com").one()
        previous = {"status": row.status, "marked_at": row.marked_at, "professional_id": row.professional_id}
        stamp = (when or clock.now()).replace(microsecond=0)
        if row.started_at is None:  # a signature presumes the patient was verified (started)
            row.started_at = stamp
            row.started_by_id = user.id
        row.status = STATUS_COMPLETED
        row.signed_at = stamp
        row.signed_by_id = user.id
        db.flush()
        appointment_signed(db, row, previous, user)  # same side effects as the endpoint
        db.commit()
    finally:
        db.close()
    return headers


def ensure_plan(client, headers, **overrides):
    """A plan (modality) with no waiting period unless overridden."""
    payload = {
        "name": "Plan Foo",
        "plan_value_cents": 9900,
        "dependent_limit": 2,
        "grace_days": 0,
        "extra_dependent_cents": 1500,
        **overrides,
    }
    existing = [item for item in client.get("/modalities", headers=headers).json() if item["name"] == payload["name"]]
    if existing:
        return existing[0]
    response = client.post("/modalities", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def activate_plan(client, headers, customer_id, *, joined_on=None, expires_on=None, plan=None, pay=True):
    """Subscribe the customer and pay every installment due up to today.

    The period is wide enough to cover both today and the fixed dates used by
    the appointment tests (October 2026)."""
    from datetime import date, timedelta

    today = clock.today()
    anchor = min(today, date(2026, 10, 1))
    joined_on = joined_on or (anchor - timedelta(days=40))
    expires_on = expires_on or (max(today, date(2026, 12, 31)) + timedelta(days=400))
    plan = plan or ensure_plan(client, headers)
    created = client.post(
        "/subscriptions",
        headers=headers,
        json={
            "customer_id": customer_id,
            "modality_id": plan["id"],
            "joined_on": joined_on.isoformat(),
            "expires_on": expires_on.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    subscription = created.json()
    if pay:
        for item in subscription["installments"]:
            if date.fromisoformat(item["due_on"]) <= today:
                paid = client.put(
                    f"/subscriptions/{subscription['id']}/installments/{item['id']}",
                    headers=headers,
                    json={"paid_on": item["due_on"]},
                )
                assert paid.status_code == 200, paid.text
                subscription = paid.json()
    return subscription

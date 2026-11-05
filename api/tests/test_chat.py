"""API tests for the end-to-end encrypted chat of remote appointments: the
two parties exchange keys through the server without the server ever being
able to read a message or an attachment; the staff is kept out; key
rotation is handled by the other party; the chat opens and closes with the
appointment; notifications carry no content."""

import os
from datetime import datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag

from app import clock
from app.chat import STORAGE_DIR, safety_code
from app.worker import tick
from chat_client import Party
from conftest import activate_plan, auth, auth_as, ensure_plan

MON_9 = datetime(2026, 10, 26, 9, 0)
MON_10 = datetime(2026, 10, 26, 10, 0)
PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(200)


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers, discount_percent=20)
    created = client.post(
        "/professionals",
        headers=headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "remote_enabled": True, "working_hours": [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]},
    )
    assert created.status_code == 201, created.text
    c.professional = created.json()
    c.pro = auth_as(client, "professional", professional_id=c.professional["id"])
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)
    c.patient = auth_as(client, "client", customer_id=c.holder)

    def book(when=MON_9, remote=True):
        response = client.post("/appointments", headers=headers, json={"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": when.isoformat(), "remote": remote})
        assert response.status_code == 201, response.text
        return response.json()

    c.appointment = book()
    c.book = book

    def chat(headers, appointment=None):
        return client.get(f"/appointments/{(appointment or c.appointment)['id']}/chat", headers=headers)

    def share(headers, keys, appointment=None):
        return client.post(f"/appointments/{(appointment or c.appointment)['id']}/chat/keys", headers=headers, json={"keys": keys})

    def send(headers, message, appointment=None):
        return client.post(f"/appointments/{(appointment or c.appointment)['id']}/chat/messages", headers=headers, json=message)

    def messages(headers, after=0, appointment=None):
        return client.get(f"/appointments/{(appointment or c.appointment)['id']}/chat/messages?after={after}", headers=headers)

    c.chat, c.share, c.send, c.messages = chat, share, send, messages

    def setup_parties():
        """Both browsers publish their keys; the patient creates the conversation key and wraps it for both."""
        patient, pro = Party(), Party()
        patient.publish(client, c.patient)
        pro.publish(client, c.pro)
        conversation = c.chat(c.patient).json()
        patient.new_conversation_key()
        keys = [patient.wrap_for(person["public_key"]) for person in conversation["participants"] if person["public_key"]]
        shared = c.share(c.patient, keys)
        assert shared.status_code == 200, shared.text
        pro.unwrap(c.chat(c.pro).json()["my_key"])
        return patient, pro, conversation["id"]

    c.setup_parties = setup_parties
    return c


# --- identity keys ---------------------------------------------------------------------------


def test_users_publish_their_identity_key(ctx):
    assert ctx.client.get("/chat/key", headers=ctx.patient).json() == {"public_key": None, "fingerprint": None, "updated_at": None}
    party = Party()
    published = party.publish(ctx.client, ctx.patient)
    assert published["public_key"] == party.jwk
    assert ctx.client.get("/chat/key", headers=ctx.patient).json()["fingerprint"] == party.fingerprint
    # Only EC P-256 keys; the private part is never sent.
    bad = ctx.client.put("/chat/key", headers=ctx.patient, json={"public_key": {"kty": "RSA", "n": "x", "e": "AQAB"}})
    assert bad.status_code == 400
    assert ctx.client.put("/chat/key", headers=ctx.patient, json={"public_key": {**party.jwk, "d": "secret"}}).json()["public_key"] == party.jwk


# --- who takes part ---------------------------------------------------------------------------


def test_only_the_patient_and_the_professional_take_part(ctx):
    for headers in (ctx.headers, auth_as(ctx.client, "supervisor"), auth_as(ctx.client, "attendant")):
        refused = ctx.chat(headers)
        assert refused.status_code == 403, refused.text
        assert "end-to-end encrypted" in refused.json()["detail"]
    other = ctx.client.post("/professionals", headers=ctx.headers, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}).json()
    assert ctx.chat(auth_as(ctx.client, "professional", professional_id=other["id"])).status_code == 403
    stranger = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    assert ctx.chat(auth_as(ctx.client, "client", customer_id=stranger)).status_code == 403
    assert ctx.chat(ctx.patient).status_code == 200
    assert ctx.chat(ctx.pro).status_code == 200


def test_the_chat_exists_for_remote_appointments_only(ctx):
    in_person = ctx.book(MON_10, remote=False)
    refused = ctx.chat(ctx.patient, in_person)
    assert refused.status_code == 409 and "remote appointments only" in refused.json()["detail"]


def test_conversation_view_before_any_key(ctx):
    body = ctx.chat(ctx.patient).json()
    assert body["open"] is True and body["closed_reason"] == ""
    assert body["open_until"].startswith("2026-11-25T09:00")
    assert body["my_fingerprint"] is None and body["my_key"] is None
    assert body["has_conversation_key"] is False and body["keys_pending"] == [] and body["safety_code"] is None
    assert {p["side"] for p in body["participants"]} == {"customer", "professional"}
    assert all(p["fingerprint"] is None and p["has_key"] is False for p in body["participants"])


# --- the key exchange ------------------------------------------------------------------------


def test_the_first_party_creates_the_key_and_wraps_it_for_everyone(ctx):
    patient, pro = Party(), Party()
    patient.publish(ctx.client, ctx.patient)
    pro.publish(ctx.client, ctx.pro)
    view = ctx.chat(ctx.patient).json()
    assert view["keys_pending"] == []  # no key yet: nothing pending
    assert view["safety_code"] == safety_code([patient.fingerprint, pro.fingerprint])
    assert ctx.chat(ctx.pro).json()["safety_code"] == view["safety_code"]  # the same on both sides
    patient.new_conversation_key()
    # The creator must wrap for itself too.
    refused = ctx.share(ctx.patient, [patient.wrap_for(pro.jwk)])
    assert refused.status_code == 400 and "your own key" in refused.json()["detail"]
    shared = ctx.share(ctx.patient, [patient.wrap_for(pro.jwk), patient.wrap_for(patient.jwk)]).json()
    assert shared["has_conversation_key"] is True and shared["keys_pending"] == []
    assert all(p["has_key"] for p in shared["participants"])
    # Each side opens its own envelope and gets the same key.
    assert pro.unwrap(ctx.chat(ctx.pro).json()["my_key"]) == patient.conversation_key
    fresh = Party()
    fresh.private_key, fresh.jwk = patient.private_key, patient.jwk
    assert fresh.unwrap(ctx.chat(ctx.patient).json()["my_key"]) == patient.conversation_key
    # An envelope is never replaced, and goes to participants' current keys only.
    original = ctx.chat(ctx.pro).json()["my_key"]["wrapped_key"]
    again = ctx.share(ctx.patient, [patient.wrap_for(pro.jwk)])
    assert again.status_code == 200
    assert ctx.chat(ctx.pro).json()["my_key"]["wrapped_key"] == original
    outsider = Party()
    assert ctx.share(ctx.patient, [patient.wrap_for(outsider.jwk)]).status_code == 400


def test_a_party_without_the_key_cannot_hand_out_keys_or_send(ctx):
    patient, pro = Party(), Party()
    patient.publish(ctx.client, ctx.patient)
    pro.publish(ctx.client, ctx.pro)
    patient.new_conversation_key()
    ctx.share(ctx.patient, [patient.wrap_for(patient.jwk)])  # the patient kept the key to itself for now
    assert ctx.chat(ctx.pro).json()["my_key"] is None
    assert ctx.chat(ctx.patient).json()["keys_pending"] == [pro.fingerprint]
    impostor = Party()
    impostor.conversation_key = os.urandom(32)
    refused = ctx.share(ctx.pro, [impostor.wrap_for(pro.jwk)])
    assert refused.status_code == 403 and "do not hold the conversation key" in refused.json()["detail"]
    refused = ctx.send(ctx.pro, impostor.encrypt_message(1, "hello"))
    assert refused.status_code == 409 and "do not hold the conversation key" in refused.json()["detail"]
    # Once the patient wraps it for the professional, it works.
    ctx.share(ctx.patient, [patient.wrap_for(pro.jwk)])
    assert pro.unwrap(ctx.chat(ctx.pro).json()["my_key"]) == patient.conversation_key


def test_a_rotated_key_gets_a_new_envelope_from_the_other_party(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    sent = ctx.send(ctx.pro, pro.encrypt_message(conversation_id, "before the reset"))
    assert sent.status_code == 201, sent.text
    # The patient signs in from a new device: new identity, the old envelope is useless.
    patient.rotate()
    patient.publish(ctx.client, ctx.patient)
    view = ctx.chat(ctx.patient).json()
    assert view["my_key"] is None and view["has_conversation_key"] is True
    assert ctx.send(ctx.patient, {"ciphertext": "eA==", "iv": "eA=="}).status_code == 409
    # The professional's browser sees who is missing a key and wraps it for them.
    view = ctx.chat(ctx.pro).json()
    assert view["keys_pending"] == [patient.fingerprint]
    recipient = next(p for p in view["participants"] if p["fingerprint"] == patient.fingerprint)
    assert ctx.share(ctx.pro, [pro.wrap_for(recipient["public_key"])]).status_code == 200
    patient.unwrap(ctx.chat(ctx.patient).json()["my_key"])
    history = ctx.messages(ctx.patient).json()
    assert patient.decrypt_message(conversation_id, history[0])["text"] == "before the reset"
    assert ctx.chat(ctx.pro).json()["keys_pending"] == []


# --- messages: the server relays ciphertext it cannot read ---------------------------------------


def test_messages_are_exchanged_and_only_the_parties_can_read_them(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    sent = ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "Doctor, the rash got worse"))
    assert sent.status_code == 201, sent.text
    first = sent.json()
    assert first["mine"] is True and first["side"] == "customer" and first["sender_name"] == f"Client {ctx.holder}"
    assert "rash" not in first["ciphertext"]
    theirs = ctx.messages(ctx.pro).json()
    assert len(theirs) == 1 and theirs[0]["mine"] is False
    assert pro.decrypt_message(conversation_id, theirs[0])["text"] == "Doctor, the rash got worse"
    reply = ctx.send(ctx.pro, pro.encrypt_message(conversation_id, "Send me a photo, please")).json()
    assert patient.decrypt_message(conversation_id, ctx.messages(ctx.patient, after=first["id"]).json()[0])["text"] == "Send me a photo, please"
    # What the server stores is opaque: a third party holding the ciphertext (the database) cannot open it.
    from app.models import ChatMessage

    db = ctx.client.session_factory()
    try:
        stored = db.get(ChatMessage, first["id"])
        assert "rash" not in stored.ciphertext and "rash" not in stored.ciphertext.encode().decode("latin-1")
    finally:
        db.close()
    outsider = Party()
    outsider.conversation_key = os.urandom(32)
    with pytest.raises(InvalidTag):
        outsider.decrypt_message(conversation_id, first)
    # A message bound to another conversation does not open either.
    with pytest.raises(InvalidTag):
        patient.decrypt_message(conversation_id + 1, first)
    assert reply["id"] > first["id"]


def test_unread_counts_and_read_markers(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    a = ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "1")).json()
    b = ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "2")).json()
    assert ctx.chat(ctx.pro).json()["unread"] == 2
    assert ctx.chat(ctx.patient).json()["unread"] == 0  # own messages are read
    marked = ctx.client.post(f"/appointments/{ctx.appointment['id']}/chat/read", headers=ctx.pro, json={"last_read_message_id": a["id"]})
    assert marked.status_code == 200 and marked.json()["unread"] == 1
    ctx.client.post(f"/appointments/{ctx.appointment['id']}/chat/read", headers=ctx.pro, json={"last_read_message_id": b["id"]})
    assert ctx.chat(ctx.pro).json()["unread"] == 0 and ctx.chat(ctx.pro).json()["last_message_id"] == b["id"]


# --- attachments --------------------------------------------------------------------------------


def upload(ctx, headers, blob: bytes, appointment=None):
    return ctx.client.post(f"/appointments/{(appointment or ctx.appointment)['id']}/chat/attachments", headers=headers, files={"file": ("blob.bin", blob)})


def test_attachments_travel_encrypted_with_their_names_inside_the_message(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    blob, iv = patient.encrypt_blob(conversation_id, PNG)
    uploaded = upload(ctx, ctx.patient, blob)
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["size_bytes"] == len(blob)
    meta = [{"id": attachment["id"], "name": "rash.png", "type": "image/png", "size": len(PNG), "iv": iv}]
    sent = ctx.send(ctx.patient, {**patient.encrypt_message(conversation_id, "Here it is", meta), "attachment_ids": [attachment["id"]]})
    assert sent.status_code == 201, sent.text
    assert sent.json()["attachments"] == [{"id": attachment["id"], "size_bytes": len(blob)}]  # the server knows the size only
    received = ctx.messages(ctx.pro).json()[0]
    payload = pro.decrypt_message(conversation_id, received)
    assert payload["attachments"][0]["name"] == "rash.png" and payload["attachments"][0]["type"] == "image/png"
    downloaded = ctx.client.get(f"/appointments/{ctx.appointment['id']}/chat/attachments/{attachment['id']}", headers=ctx.pro)
    assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.content == blob and downloaded.content != PNG
    assert pro.decrypt_blob(conversation_id, downloaded.content, payload["attachments"][0]["iv"]) == PNG
    # The staff cannot download the blob either; the blob is sent once.
    assert ctx.client.get(f"/appointments/{ctx.appointment['id']}/chat/attachments/{attachment['id']}", headers=ctx.headers).status_code == 403
    again = ctx.send(ctx.patient, {**patient.encrypt_message(conversation_id, "again", meta), "attachment_ids": [attachment["id"]]})
    assert again.status_code == 400 and "already sent" in again.json()["detail"]


def test_attachment_limits(ctx, monkeypatch):
    patient, pro, conversation_id = ctx.setup_parties()
    import app.chat as module
    import app.routers.chat as router

    monkeypatch.setattr(router, "MAX_ATTACHMENT_BYTES", 256)
    assert upload(ctx, ctx.patient, b"x" * 300).status_code == 413
    assert upload(ctx, ctx.patient, b"").status_code == 422 or upload(ctx, ctx.patient, b"").status_code == 400
    ids = [upload(ctx, ctx.patient, b"x" * 10).json()["id"] for _ in range(6)]
    too_many = ctx.send(ctx.patient, {**patient.encrypt_message(conversation_id, "x"), "attachment_ids": ids})
    assert too_many.status_code == 422  # schema: at most 5 per message
    # Another participant's blob, or a blob of another conversation, cannot be referenced.
    other = ctx.book(MON_10)
    patient.new_conversation_key()
    ctx.share(ctx.patient, [patient.wrap_for(patient.jwk), patient.wrap_for(pro.jwk)], other)
    foreign = upload(ctx, ctx.patient, b"x" * 10, other).json()["id"]
    refused = ctx.send(ctx.patient, {**patient.encrypt_message(conversation_id, "x"), "attachment_ids": [foreign]})
    assert refused.status_code == 400
    refused = ctx.send(ctx.pro, {**pro.encrypt_message(conversation_id, "x"), "attachment_ids": [ids[0]]})
    assert refused.status_code == 400 and "not yours" in refused.json()["detail"]
    monkeypatch.setattr(router, "MAX_ORPHANS", 6)
    assert upload(ctx, ctx.patient, b"x" * 10).status_code == 409


# --- lifecycle ------------------------------------------------------------------------------------


def test_the_chat_closes_with_the_appointment(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "hello"))
    payload = {key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    assert ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"}).status_code == 200
    view = ctx.chat(ctx.patient).json()
    assert view["open"] is False and "cancelled" in view["closed_reason"]
    assert len(ctx.messages(ctx.patient).json()) == 1  # history stays readable
    refused = ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "still there?"))
    assert refused.status_code == 409 and "closed" in refused.json()["detail"]
    assert upload(ctx, ctx.patient, b"x").status_code == 409


def test_the_chat_closes_thirty_days_after_the_visit(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    clock.travel_to(MON_9 + timedelta(days=29))
    assert ctx.send(ctx.pro, pro.encrypt_message(conversation_id, "how are you?")).status_code == 201
    clock.travel_to(MON_9 + timedelta(days=31))
    assert ctx.send(ctx.pro, pro.encrypt_message(conversation_id, "?")).status_code == 409
    assert ctx.chat(ctx.pro).json()["closed_reason"].startswith("The chat closed 30 days")


def test_attachments_follow_the_retention_and_orphans_are_purged(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    blob, iv = patient.encrypt_blob(conversation_id, PNG)
    sent_id = upload(ctx, ctx.patient, blob).json()["id"]
    ctx.send(ctx.patient, {**patient.encrypt_message(conversation_id, "x"), "attachment_ids": [sent_id]})
    orphan_id = upload(ctx, ctx.patient, blob).json()["id"]
    before = len(os.listdir(STORAGE_DIR))
    db = ctx.client.session_factory()
    try:
        # The next day: the orphan goes, the sent one stays.
        assert tick(db, MON_9 + timedelta(days=2))["chat"] == {"expired": 0, "orphans": 1}
        assert len(os.listdir(STORAGE_DIR)) == before - 1
        # Ten years after the visit: the attachments go, the (encrypted) messages stay.
        assert tick(db, datetime(2036, 10, 27, 3, 0))["chat"] == {"expired": 1, "orphans": 0}
    finally:
        db.close()
    assert len(os.listdir(STORAGE_DIR)) == before - 2
    assert ctx.client.get(f"/appointments/{ctx.appointment['id']}/chat/attachments/{sent_id}", headers=ctx.pro).status_code == 404
    assert ctx.client.get(f"/appointments/{ctx.appointment['id']}/chat/attachments/{orphan_id}", headers=ctx.pro).status_code == 404
    assert len(ctx.messages(ctx.pro).json()) == 1


def test_deleting_the_appointment_deletes_the_chat(ctx):
    patient, pro, conversation_id = ctx.setup_parties()
    blob, iv = patient.encrypt_blob(conversation_id, PNG)
    upload(ctx, ctx.patient, blob)
    before = len(os.listdir(STORAGE_DIR))
    assert ctx.client.delete(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers).status_code == 204
    assert len(os.listdir(STORAGE_DIR)) == before - 1
    from app.models import Conversation

    db = ctx.client.session_factory()
    try:
        assert db.query(Conversation).filter(Conversation.id == conversation_id).first() is None
    finally:
        db.close()


# --- notifications (no content) ----------------------------------------------------------------------


def test_new_messages_notify_the_other_side_without_content(ctx, outbox):
    patient, pro, conversation_id = ctx.setup_parties()
    ctx.client.post("/notifications/read-all", headers=ctx.pro)
    ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "a secret complaint"))
    theirs = ctx.client.get("/notifications?unread_only=true", headers=ctx.pro).json()
    assert theirs[0]["event"] == "chat.message"
    assert theirs[0]["title"] == "Foo Holder sent you a message"
    assert "secret" not in theirs[0]["body"] and "end-to-end encrypted" in theirs[0]["body"]
    # Throttled: a second message within 30 minutes adds no notification; after that it does.
    ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "more"))
    assert len([n for n in ctx.client.get("/notifications", headers=ctx.pro).json() if n["event"] == "chat.message"]) == 1
    clock.travel_to(clock.now() + timedelta(minutes=31))
    ctx.send(ctx.patient, patient.encrypt_message(conversation_id, "later"))
    assert len([n for n in ctx.client.get("/notifications", headers=ctx.pro).json() if n["event"] == "chat.message"]) == 2
    # The professional's reply reaches the patient in-app and by email (queued), content-free.
    ctx.send(ctx.pro, pro.encrypt_message(conversation_id, "reply"))
    mine = ctx.client.get("/notifications", headers=ctx.patient).json()
    assert mine[0]["event"] == "chat.message" and mine[0]["title"] == "Lorem Ipsum sent you a message"
    jobs = ctx.client.get("/queue?status=pending", headers=ctx.headers).json()
    assert any(job["event"] == "chat.message" for job in jobs)

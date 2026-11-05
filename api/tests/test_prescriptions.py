"""API tests for the medical prescriptions attached to appointments: only
text documents (checked on the content), up to five per appointment, read by
everyone with access to the appointment through signed links that expire
within 30 days, deleted with a cancelled appointment and purged at the end
of the 10-year retention."""

import io
import os
import zipfile
from datetime import datetime, timedelta

import pytest

from app import clock
from app.prescriptions import STORAGE_DIR, purge_expired, sniff, PrescriptionError
from app.worker import tick
from conftest import activate_plan, auth, auth_as, ensure_plan
from pki import CPF, PASSWORD, certificate

MON_9 = datetime(2026, 10, 26, 9, 0)
MON_10 = datetime(2026, 10, 26, 10, 0)

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
TXT = "Dipirona 500 mg, 1 comprimido de 6/6 h por 3 dias.\n".encode("utf-8")
RTF = b"{\\rtf1\\ansi Dipirona 500 mg}"
DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
EXE = b"MZ\x90\x00" + b"\x00" * 32


def office(kind: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if kind == "docx":
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<w:document/>")
        elif kind == "odt":
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("content.xml", "<office:document-content/>")
        elif kind == "xlsx":
            archive.writestr("xl/workbook.xml", "<workbook/>")
        else:
            archive.writestr("photo.png", PNG)
    return buffer.getvalue()


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
        json={"name": "Lorem Ipsum", "document": "123.456.789-09", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "working_hours": [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]},
    )
    assert created.status_code == 201, created.text
    c.professional = created.json()
    c.login = auth_as(client, "professional", professional_id=c.professional["id"])
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)
    c.patient = auth_as(client, "client", customer_id=c.holder)

    def book(when=MON_9, **overrides):
        payload = {"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": when.isoformat(), **overrides}
        response = client.post("/appointments", headers=headers, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    c.appointment = book()

    def upload(content=PDF, filename="receita.pdf", headers=headers, appointment=None, note=None):
        item = appointment or c.appointment
        data = {"note": note} if note is not None else None
        return client.post(f"/appointments/{item['id']}/prescriptions", headers=headers, files={"file": (filename, content)}, data=data)

    def listing(headers=headers, appointment=None):
        item = appointment or c.appointment
        return client.get(f"/appointments/{item['id']}/prescriptions", headers=headers)

    def sign(document, headers=None, pfx=None, password=PASSWORD):
        return client.post(
            f"/appointments/{document['appointment_id']}/prescriptions/{document['id']}/sign",
            headers=headers or c.login,
            files={"certificate": ("lorem.pfx", pfx or certificate())},
            data={"password": password},
        )

    c.book, c.upload, c.listing, c.sign = book, upload, listing, sign
    return c


# --- type check (pure) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "content, kind",
    [(PDF, "pdf"), (TXT, "txt"), (RTF, "rtf"), (DOC, "doc"), (office("docx"), "docx"), (office("odt"), "odt")],
)
def test_sniff_accepts_text_documents(content, kind):
    assert sniff(content) == kind


@pytest.mark.parametrize("content", [PNG, EXE, office("xlsx"), office("zip"), b"\x00\x01\x02", b"caf\xe9 latin-1 not utf-8"])
def test_sniff_refuses_everything_else(content):
    with pytest.raises(PrescriptionError) as excinfo:
        sniff(content)
    assert excinfo.value.status_code == 415


def test_sniff_refuses_an_empty_file():
    with pytest.raises(PrescriptionError):
        sniff(b"")


# --- attaching -----------------------------------------------------------------------


def test_staff_attaches_a_pdf_prescription(ctx):
    created = ctx.upload(note="Post-visit prescription")
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["filename"] == "receita.pdf"
    assert item["content_type"] == "application/pdf"
    assert item["size_bytes"] == len(PDF)
    assert item["note"] == "Post-visit prescription"
    assert item["uploaded_by_name"] == "Foo Bar"
    assert item["download_url"].startswith(f"/appointments/{ctx.appointment['id']}/prescriptions/{item['id']}/download?token=")
    assert item["retain_until"] == "2036-10-26"  # ten years after the visit
    listed = ctx.listing().json()
    assert [row["id"] for row in listed["items"]] == [item["id"]]
    assert listed["remaining"] == 4 and listed["max_files"] == 5 and listed["can_attach"] is True
    assert ctx.client.get(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers).json()["prescriptions_count"] == 1


def test_the_type_is_decided_by_the_content_not_the_name(ctx):
    refused = ctx.upload(PNG, "receita.pdf")
    assert refused.status_code == 415
    assert "only text documents" in refused.json()["detail"]
    assert ctx.upload(EXE, "receita.txt").status_code == 415
    assert ctx.upload(office("xlsx"), "receita.docx").status_code == 415
    assert ctx.upload(b"", "empty.pdf").status_code == 400
    accepted = ctx.upload(TXT, "receita.jpg")  # plain text, whatever the name says
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["filename"] == "receita.txt" and accepted.json()["content_type"].startswith("text/plain")
    for content, name in ((RTF, "a.rtf"), (DOC, "b.doc"), (office("docx"), "c.docx"), (office("odt"), "d.odt")):
        assert ctx.upload(content, name).status_code == 201


def test_file_names_are_sanitised(ctx):
    from app.prescriptions import safe_filename

    item = ctx.upload(PDF, "../../etc/passwd.exe").json()
    assert item["filename"] == "passwd.pdf"
    item = ctx.upload(PDF, ".pdf").json()  # no usable name
    assert item["filename"] == "prescription.pdf"
    assert safe_filename("..\\evil\x00 name\".txt", "txt") == "evil name.txt"
    assert safe_filename("x" * 300 + ".pdf", "pdf") == "x" * 120 + ".pdf"


def test_at_most_five_files_per_appointment(ctx):
    for index in range(5):
        assert ctx.upload(PDF, f"r{index}.pdf").status_code == 201
    refused = ctx.upload(PDF, "sixth.pdf")
    assert refused.status_code == 409
    assert "at most 5" in refused.json()["detail"]
    listed = ctx.listing().json()
    assert listed["remaining"] == 0 and len(listed["items"]) == 5
    # Removing one frees a place.
    assert ctx.client.delete(f"/appointments/{ctx.appointment['id']}/prescriptions/{listed['items'][0]['id']}", headers=ctx.headers).status_code == 204
    assert ctx.upload(PDF, "sixth.pdf").status_code == 201


def test_size_limit(ctx, monkeypatch):
    import app.prescriptions as module

    monkeypatch.setattr(module, "MAX_BYTES", 100)
    assert ctx.upload(PDF + b"x" * 100).status_code == 413
    assert ctx.upload(PDF).status_code == 201


def test_who_attaches_and_removes(ctx):
    # The appointment's professional and any staff member; patients only read.
    mine = ctx.upload(headers=ctx.login)
    assert mine.status_code == 201, mine.text
    assert ctx.upload(headers=auth_as(ctx.client, "supervisor")).status_code == 201
    assert ctx.upload(headers=auth_as(ctx.client, "attendant")).status_code == 201
    assert ctx.upload(headers=ctx.patient).status_code == 403
    other = ctx.client.post(
        "/professionals", headers=ctx.headers, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()
    stranger = auth_as(ctx.client, "professional", professional_id=other["id"])
    assert ctx.upload(headers=stranger).status_code == 403
    assert ctx.listing(headers=stranger).status_code == 403
    doc = mine.json()
    path = f"/appointments/{ctx.appointment['id']}/prescriptions/{doc['id']}"
    assert ctx.client.delete(path, headers=ctx.patient).status_code == 403
    assert ctx.sign(doc).status_code == 200  # a patient only sees signed prescriptions
    assert ctx.listing(headers=ctx.patient).json()["items"][0]["can_delete"] is False
    assert ctx.client.delete(path, headers=ctx.login).status_code == 204
    assert ctx.client.delete(path, headers=ctx.login).status_code == 404


def test_nothing_is_attached_to_a_cancelled_appointment(ctx):
    payload = {key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    assert ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"}).status_code == 200
    refused = ctx.upload()
    assert refused.status_code == 409 and "Cancelled" in refused.json()["detail"]
    assert ctx.listing().json()["can_attach"] is False


# --- reading: everyone with access, through links that expire -------------------------------


def test_everyone_with_access_to_the_appointment_reads_the_files(ctx):
    assert ctx.sign(ctx.upload().json()).status_code == 200  # patients see signed prescriptions (see test_prescription_signing)
    for headers in (ctx.headers, auth_as(ctx.client, "supervisor"), auth_as(ctx.client, "attendant"), ctx.login, ctx.patient):
        listed = ctx.listing(headers=headers)
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 1
        download = ctx.client.get(listed.json()["items"][0]["download_url"])  # no bearer: the link is the credential
        assert download.status_code == 200, download.text
        assert download.content == PDF
        assert download.headers["content-type"] == "application/pdf"
        assert 'filename="receita.pdf"' in download.headers["content-disposition"]
    # Another customer's login, and sellers, are kept out.
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    assert ctx.listing(headers=auth_as(ctx.client, "client", customer_id=other)).status_code == 403


def test_links_expire_within_thirty_days(ctx):
    item = ctx.upload().json()
    expires = datetime.fromisoformat(item["link_expires_at"])
    assert expires == clock.now().replace(microsecond=0) + timedelta(days=30)
    clock.travel_to(clock.now() + timedelta(days=29, hours=23))
    assert ctx.client.get(item["download_url"]).status_code == 200
    clock.travel_to(clock.now() + timedelta(hours=2))
    expired = ctx.client.get(item["download_url"])
    assert expired.status_code == 403
    assert "expired" in expired.json()["detail"]
    # Listing again issues a fresh link.
    fresh = ctx.listing().json()["items"][0]
    assert fresh["download_url"] != item["download_url"]
    assert ctx.client.get(fresh["download_url"]).status_code == 200


def test_tampered_or_foreign_links_are_refused(ctx):
    item = ctx.upload().json()
    base, token = item["download_url"].split("?token=")
    expires, signature = token.split(".")
    later = str(int(expires) + 86400 * 365)
    assert ctx.client.get(f"{base}?token={later}.{signature}").status_code == 403
    assert ctx.client.get(f"{base}?token={expires}.{'0' * 64}").status_code == 403
    assert ctx.client.get(f"{base}?token=garbage").status_code == 403
    assert ctx.client.get(base).status_code == 401  # neither a link nor a login
    # A link of one file does not open another.
    second = ctx.upload(TXT, "b.txt").json()
    other_base = second["download_url"].split("?token=")[0]
    assert ctx.client.get(f"{other_base}?token={token}").status_code == 403


def test_download_with_a_login_that_has_access(ctx):
    item = ctx.sign(ctx.upload().json()).json()
    base = item["download_url"].split("?token=")[0]
    assert ctx.client.get(base, headers=ctx.patient).status_code == 200
    assert ctx.client.get(base, headers=ctx.login).status_code == 200
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()["id"]
    assert ctx.client.get(base, headers=auth_as(ctx.client, "client", customer_id=other)).status_code == 403


# --- lifecycle: cancellation, deletion, retention --------------------------------------------


def stored_files(ctx):
    return {doc["download_url"] for doc in ctx.listing().json()["items"]}


def test_cancelling_the_appointment_deletes_its_files(ctx):
    first = ctx.upload().json()
    ctx.upload(TXT, "b.txt")
    before = len(os.listdir(STORAGE_DIR))
    payload = {key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    cancelled = ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["prescriptions_count"] == 0
    assert ctx.listing().json()["items"] == []
    assert len(os.listdir(STORAGE_DIR)) == before - 2  # the content is gone too
    assert ctx.client.get(first["download_url"]).status_code == 404  # even an unexpired link
    # Re-activating brings the slot back, not the files.
    assert ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "agendada"}).status_code == 200
    assert ctx.listing().json()["items"] == []


def test_editing_notes_keeps_the_files(ctx):
    ctx.upload()
    payload = {key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at")}
    edited = ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "agendada", "notes": "x"})
    assert edited.status_code == 200 and edited.json()["prescriptions_count"] == 1


def test_deleting_the_appointment_deletes_its_files(ctx):
    item = ctx.upload().json()
    before = len(os.listdir(STORAGE_DIR))
    assert ctx.client.delete(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers).status_code == 204
    assert len(os.listdir(STORAGE_DIR)) == before - 1
    assert ctx.client.get(item["download_url"]).status_code == 404


def test_files_are_purged_after_the_legal_retention(ctx):
    item = ctx.upload().json()
    assert item["retain_until"] == "2036-10-26"
    db = ctx.client.session_factory()
    try:
        assert purge_expired(db, datetime(2036, 10, 26).date()) == 0  # last day of the retention: still kept
        assert purge_expired(db, datetime(2036, 10, 27).date()) == 1
        assert purge_expired(db, datetime(2036, 10, 27).date()) == 0  # idempotent
    finally:
        db.close()
    assert ctx.listing().json()["items"] == []
    assert ctx.client.get(item["download_url"]).status_code == 404


def test_the_worker_runs_the_retention_purge(ctx):
    ctx.upload()
    db = ctx.client.session_factory()
    try:
        result = tick(db, datetime(2036, 10, 27, 3, 0))
    finally:
        db.close()
    assert result["prescriptions"] == {"purged": 1}
    assert ctx.listing().json()["items"] == []


def test_retention_follows_the_date_of_the_visit(ctx):
    moved = ctx.client.put(
        f"/appointments/{ctx.appointment['id']}",
        headers=ctx.headers,
        json={**{key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "notes")}, "marked_at": MON_10.replace(day=27).isoformat(), "status": "agendada"},
    )
    assert moved.status_code == 200, moved.text
    assert ctx.upload().json()["retain_until"] == "2036-10-27"


# --- notifications ------------------------------------------------------------------


def test_the_professional_is_asked_to_sign_what_the_staff_attached(ctx, outbox):
    outbox.sent.clear()
    ctx.upload()
    theirs = ctx.client.get("/notifications", headers=ctx.login).json()
    assert theirs[0]["event"] == "appointment.prescription"
    assert theirs[0]["title"] == "Prescription awaiting your signature: Foo Holder, 26/10/2026 at 09:00"
    assert "File: receita.pdf" in theirs[0]["body"] and theirs[0]["link"] == f"/appointments/{ctx.appointment['id']}"
    # The patient hears nothing of a draft; the professional attaching its own is not told either.
    assert not any(n["event"].startswith("appointment.prescription") for n in ctx.client.get("/notifications", headers=ctx.patient).json())
    ctx.upload(TXT, "b.txt", headers=ctx.login)
    theirs = ctx.client.get("/notifications", headers=ctx.login).json()
    assert [n["event"] for n in theirs].count("appointment.prescription") == 1

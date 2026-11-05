"""API tests for the digital signature of prescriptions: only the
appointment's professional signs, with an ICP-Brasil-shaped certificate
that chains to the trust bundle, is valid at the signing time and carries
the professional's CPF; the CAdES detached signature is verified by the
system (and by OpenSSL) from the stored file and .p7s; patients only see
signed prescriptions; a public check serves the pharmacies."""

import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

import pytest

from app import clock
from app.prescriptions import STORAGE_DIR
from conftest import activate_plan, auth, auth_as, ensure_plan
from pki import CPF, PASSWORD, certificate, expired_certificate, pki, untrusted_certificate

MON_9 = datetime(2026, 10, 26, 9, 0)
MON_10 = datetime(2026, 10, 26, 10, 0)
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
TXT = "Amoxicilina 500 mg, 8/8 h, 7 dias.\n".encode()


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.plan = ensure_plan(client, headers, discount_percent=20)

    def professional(name, document):
        created = client.post(
            "/professionals",
            headers=headers,
            json={"name": name, "document": document, "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, "working_hours": [{"weekday": w, "start": "08:00", "end": "12:00"} for w in range(5)]},
        )
        assert created.status_code == 201, created.text
        return created.json()

    c.professional = professional("Lorem Ipsum", "123.456.789-09")
    c.other = professional("Dolor Sit", "98765432100")
    c.login = auth_as(client, "professional", professional_id=c.professional["id"])
    c.other_login = auth_as(client, "professional", professional_id=c.other["id"])
    c.holder = client.post("/customers", headers=headers, json={"name": "Foo Holder", "email": "holder@example.com"}).json()["id"]
    activate_plan(client, headers, c.holder, plan=c.plan)
    c.patient = auth_as(client, "client", customer_id=c.holder)
    booked = client.post("/appointments", headers=headers, json={"customer_id": c.holder, "professional_id": c.professional["id"], "marked_at": MON_9.isoformat()})
    assert booked.status_code == 201, booked.text
    c.appointment = booked.json()

    def upload(content=PDF, filename="receita.pdf", headers=headers):
        response = client.post(f"/appointments/{c.appointment['id']}/prescriptions", headers=headers, files={"file": (filename, content)})
        assert response.status_code == 201, response.text
        return response.json()

    def sign(document, headers=None, pfx=None, password=PASSWORD):
        return client.post(
            f"/appointments/{document['appointment_id']}/prescriptions/{document['id']}/sign",
            headers=headers or c.login,
            files={"certificate": ("lorem.pfx", pfx if pfx is not None else certificate())},
            data={"password": password},
        )

    def report(document, headers=None):
        return client.get(f"/appointments/{document['appointment_id']}/prescriptions/{document['id']}/signature", headers=headers or c.login)

    def listing(headers=headers):
        return client.get(f"/appointments/{c.appointment['id']}/prescriptions", headers=headers).json()

    c.upload, c.sign, c.report, c.listing = upload, sign, report, listing
    return c


# --- who signs ---------------------------------------------------------------------------


def test_the_appointments_professional_signs_with_its_certificate(ctx):
    draft = ctx.upload()
    assert draft["signed"] is False and draft["can_sign"] is False  # the staff's view
    mine = ctx.listing(headers=ctx.login)["items"][0]
    assert mine["can_sign"] is True
    signed = ctx.sign(draft)
    assert signed.status_code == 200, signed.text
    item = signed.json()
    assert item["signed"] is True and item["can_sign"] is False
    assert item["signed_at"] == clock.now().replace(microsecond=0).isoformat()
    assert item["signer_document"] == CPF
    assert item["signer_name"] == f"Lorem Ipsum:{CPF}"
    assert item["certificate_issuer"] == "AC teste de Pessoas"
    assert item["certificate_serial"] and item["certificate_not_after"].startswith("2040-06-01")
    assert item["signature_url"].startswith(f"/appointments/{ctx.appointment['id']}/prescriptions/{item['id']}/signature.p7s?token=")
    assert item["signature_policy"] == ""  # no ICP-Brasil policy configured in the tests


def test_nobody_else_signs(ctx):
    draft = ctx.upload()
    for headers in (ctx.headers, auth_as(ctx.client, "supervisor"), auth_as(ctx.client, "attendant"), ctx.patient):
        refused = ctx.sign(draft, headers=headers)
        assert refused.status_code == 403, refused.text
        assert "Only the appointment's professional" in refused.json()["detail"]
    assert ctx.sign(draft, headers=ctx.other_login).status_code == 403  # another professional: not its appointment
    assert ctx.listing(headers=ctx.login)["items"][0]["signed"] is False


def test_signing_twice_or_on_a_cancelled_appointment_is_refused(ctx):
    draft = ctx.upload()
    assert ctx.sign(draft).status_code == 200
    again = ctx.sign(draft)
    assert again.status_code == 409 and "already signed" in again.json()["detail"]
    second = ctx.upload(TXT, "b.txt")
    payload = {key: ctx.appointment[key] for key in ("customer_id", "dependent_id", "professional_id", "marked_at", "notes")}
    assert ctx.client.put(f"/appointments/{ctx.appointment['id']}", headers=ctx.headers, json={**payload, "status": "cancelada"}).status_code == 200
    assert ctx.sign(second).status_code == 404  # the files went with the cancellation


# --- the certificate ----------------------------------------------------------------------


def test_the_certificate_must_carry_the_professionals_cpf(ctx):
    draft = ctx.upload()
    someone_else = ctx.sign(draft, pfx=certificate("Dolor Sit", "98765432100"))
    assert someone_else.status_code == 403
    assert "belongs to another person" in someone_else.json()["detail"]
    no_cpf = ctx.sign(draft, pfx=certificate("Anonymous", None))
    assert no_cpf.status_code == 400 and "carries no CPF" in no_cpf.json()["detail"]
    assert ctx.listing(headers=ctx.login)["items"][0]["signed"] is False


def test_a_professional_without_cpf_on_record_cannot_sign(ctx):
    updated = ctx.client.put(
        f"/professionals/{ctx.professional['id']}",
        headers=ctx.headers,
        json={"name": "Lorem Ipsum", "document": "", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000},
    )
    assert updated.status_code == 200 and updated.json()["document"] == ""
    refused = ctx.sign(ctx.upload())
    assert refused.status_code == 409 and "Register your CPF" in refused.json()["detail"]


def test_untrusted_expired_or_unreadable_certificates_are_refused(ctx):
    draft = ctx.upload()
    untrusted = ctx.sign(draft, pfx=untrusted_certificate())
    assert untrusted.status_code == 400
    assert "could not be verified" in untrusted.json()["detail"] and "chain" in untrusted.json()["detail"]
    expired = ctx.sign(draft, pfx=expired_certificate())
    assert expired.status_code == 400 and "validity" in expired.json()["detail"]
    wrong_password = ctx.sign(draft, password="nope")
    assert wrong_password.status_code == 400 and "wrong password" in wrong_password.json()["detail"]
    garbage = ctx.sign(draft, pfx=b"not a pkcs12 file")
    assert garbage.status_code == 400
    assert ctx.sign(draft).status_code == 200  # the right one still works


def test_signing_needs_the_trust_bundle(ctx, monkeypatch):
    import app.prescriptions as module
    from pathlib import Path

    monkeypatch.setattr(module, "TRUST_BUNDLE", Path("/nonexistent/roots.pem"))
    draft = ctx.upload()
    assert ctx.listing(headers=ctx.login)["signing_available"] is False
    refused = ctx.sign(draft)
    assert refused.status_code == 503 and "trust bundle" in refused.json()["detail"]


# --- verification by the system ------------------------------------------------------------


def test_the_system_verifies_the_stored_signature(ctx):
    item = ctx.sign(ctx.upload()).json()
    report = ctx.report(item)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["valid"] is True
    names = {check["name"]: check for check in body["checks"]}
    for name in ("structure", "signer-certificate", "algorithms", "content-type", "message-digest", "signing-time", "signing-certificate-v2", "signature", "chain", "validity", "stored-files", "signer"):
        assert names[name]["ok"] is True, names[name]
    assert body["chain"] == [f"Lorem Ipsum:{CPF}", "AC teste de Pessoas", "AC Raiz teste"]
    assert body["signer_document"] == CPF
    assert body["digest_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["algorithm"] == "SHA-256 with RSA (PKCS#1 v1.5)"
    assert body["standard"].startswith("CAdES-BES detached")
    # Everyone with access reads the report; a draft has none for the patient.
    assert ctx.report(item, headers=ctx.patient).json()["valid"] is True
    assert ctx.report(item, headers=ctx.headers).json()["valid"] is True
    draft = ctx.upload(TXT, "b.txt")
    assert ctx.report(draft, headers=ctx.patient).status_code == 404
    assert ctx.report(draft).json()["valid"] is False


def test_a_tampered_file_or_signature_fails_verification(ctx):
    item = ctx.sign(ctx.upload()).json()
    db = ctx.client.session_factory()
    try:
        from app.models import AppointmentDocument

        row = db.get(AppointmentDocument, item["id"])
        file_key, signature_key = row.storage_key, row.signature_key
    finally:
        db.close()
    original = (STORAGE_DIR / file_key).read_bytes()
    (STORAGE_DIR / file_key).write_bytes(original + b"\n% altered")
    body = ctx.report(item).json()
    assert body["valid"] is False
    failed = {check["name"] for check in body["checks"] if not check["ok"]}
    assert "message-digest" in failed and "stored-files" in failed
    (STORAGE_DIR / file_key).write_bytes(original)
    assert ctx.report(item).json()["valid"] is True
    signature = (STORAGE_DIR / signature_key).read_bytes()
    (STORAGE_DIR / signature_key).write_bytes(signature[:-1] + bytes([signature[-1] ^ 0x01]))
    body = ctx.report(item).json()
    assert body["valid"] is False and "stored-files" in {c["name"] for c in body["checks"] if not c["ok"]}


def test_the_signature_file_is_downloadable_and_verifiable_outside(ctx):
    item = ctx.sign(ctx.upload()).json()
    p7s = ctx.client.get(item["signature_url"])
    assert p7s.status_code == 200
    assert p7s.headers["content-type"] == "application/pkcs7-signature"
    assert 'filename="receita.p7s"' in p7s.headers["content-disposition"]
    content = ctx.client.get(item["download_url"]).content
    # Independent verification with the application's own module, then with OpenSSL.
    from app.cms import verify_signed_data

    assert verify_signed_data(p7s.content, content, [pki()["root"].certificate]).valid is True
    assert verify_signed_data(p7s.content, content + b"x", [pki()["root"].certificate]).valid is False
    if shutil.which("openssl") is None:
        pytest.skip("openssl binary not available")
    with tempfile.TemporaryDirectory() as folder:
        open(os.path.join(folder, "receita.pdf"), "wb").write(content)
        open(os.path.join(folder, "receita.p7s"), "wb").write(p7s.content)
        result = subprocess.run(
            ["openssl", "cms", "-verify", "-binary", "-inform", "DER", "-in", os.path.join(folder, "receita.p7s"),
             "-content", os.path.join(folder, "receita.pdf"), "-CAfile", os.environ["ICP_BRASIL_TRUST_BUNDLE"], "-purpose", "any", "-out", os.devnull],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Verification successful" in result.stderr
    # The .p7s needs the same link or a login; a draft has no signature to download.
    base = item["signature_url"].split("?token=")[0]
    assert ctx.client.get(base).status_code == 401
    assert ctx.client.get(base, headers=ctx.patient).status_code == 200
    draft = ctx.upload(TXT, "b.txt")
    assert ctx.client.get(f"/appointments/{ctx.appointment['id']}/prescriptions/{draft['id']}/signature.p7s", headers=ctx.login).status_code == 404


def test_signature_links_expire_like_the_file_links(ctx):
    item = ctx.sign(ctx.upload()).json()
    clock.travel_to(clock.now() + timedelta(days=31))
    assert ctx.client.get(item["signature_url"]).status_code == 403
    assert ctx.client.get(ctx.listing(headers=ctx.login)["items"][0]["signature_url"]).status_code == 200


# --- patients see signed prescriptions only ---------------------------------------------------


def test_patients_only_see_signed_prescriptions(ctx):
    draft = ctx.upload()
    assert ctx.listing(headers=ctx.patient)["items"] == []
    base = draft["download_url"].split("?token=")[0]
    refused = ctx.client.get(base, headers=ctx.patient)
    assert refused.status_code == 403 and "not signed" in refused.json()["detail"]
    ctx.sign(draft)
    mine = ctx.listing(headers=ctx.patient)["items"]
    assert [item["id"] for item in mine] == [draft["id"]] and mine[0]["signed"] is True
    assert ctx.client.get(base, headers=ctx.patient).status_code == 200


def test_the_patient_is_told_when_the_prescription_is_signed(ctx, outbox):
    draft = ctx.upload()
    outbox.sent.clear()
    ctx.sign(draft)
    mine = ctx.client.get("/notifications", headers=ctx.patient).json()
    assert mine[0]["event"] == "appointment.prescription_signed"
    assert mine[0]["title"] == "Signed prescription available for the appointment of 26/10/2026 at 09:00"
    assert f"CPF {CPF}" in mine[0]["body"] and "receita.pdf" in mine[0]["body"]
    jobs = ctx.client.get("/queue?status=pending", headers=ctx.headers).json()
    assert any(job["event"] == "appointment.prescription_signed" for job in jobs)


# --- the public check for pharmacies --------------------------------------------------------------


def test_pharmacies_verify_a_file_and_its_signature_without_a_login(ctx):
    item = ctx.sign(ctx.upload()).json()
    content = ctx.client.get(item["download_url"]).content
    p7s = ctx.client.get(item["signature_url"]).content
    checked = ctx.client.post("/prescriptions/verify", files={"file": ("receita.pdf", content), "signature": ("receita.p7s", p7s)})
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["valid"] is True
    assert body["signer_document"] == CPF
    assert body["professional_registered"] is True
    assert body["professional_name"] == "Lorem Ipsum" and body["professional_specialty"] == "Clínico geral"
    assert body["signing_time"] is not None
    # Another file with the same signature, or a broken signature: invalid, never a server error.
    wrong = ctx.client.post("/prescriptions/verify", files={"file": ("x.pdf", content + b"x"), "signature": ("receita.p7s", p7s)}).json()
    assert wrong["valid"] is False
    assert any(c["name"] == "message-digest" and not c["ok"] for c in wrong["checks"])
    broken = ctx.client.post("/prescriptions/verify", files={"file": ("x.pdf", content), "signature": ("receita.p7s", b"garbage")}).json()
    assert broken["valid"] is False and broken["professional_registered"] is False
    # A valid signature by someone outside the network is reported as such.
    from app.cms import build_signed_data
    from app.demo_pki import issue_person
    from datetime import timezone

    key, cert = issue_person(pki()["intermediate"], "Outsider", "11122233396")
    outsider = build_signed_data(content, key, cert, [pki()["intermediate"].certificate], datetime.now(timezone.utc))
    body = ctx.client.post("/prescriptions/verify", files={"file": ("x.pdf", content), "signature": ("o.p7s", outsider)}).json()
    assert body["valid"] is True and body["professional_registered"] is False and body["signer_document"] == "11122233396"


def test_professional_cpf_on_the_record(ctx):
    me = ctx.client.get("/professionals/me", headers=ctx.login).json()
    assert me["document"] == "123.456.789-09"

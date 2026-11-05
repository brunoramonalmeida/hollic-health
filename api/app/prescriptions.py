"""Medical prescriptions attached to appointments.

An appointment may carry up to ``MAX_FILES`` prescription files
(``AppointmentDocument`` rows, content in the prescriptions storage). Only
**text documents** are accepted — PDF, plain text, RTF, Word (.doc/.docx)
and OpenDocument text — and the type is decided by **sniffing the content**
(``sniff``), never by the file name or the declared content type: a JPEG
renamed to ``.pdf`` is refused.

Who does what:

* **reads**: everyone who may read the appointment (the staff, the patient's
  client login, the appointment's professional) lists the files and gets a
  **signed download link** for each one; the link is the credential (it
  opens in the browser without the bearer token) and **expires**
  ``LINK_DAYS`` after it was issued — at most 30 days. It is bound to the
  file's fingerprint, so a removed file's links stop working at once.
* **attaches / removes**: the staff and the appointment's professional.
  Nothing can be attached to a cancelled appointment.

Lifecycle:

* **cancelling** an appointment (or deleting it) **deletes its files** —
  rows and content — at once;
* the documentation is kept for the **legal retention** of
  ``RETENTION_YEARS`` (10) counted from the date of the visit; the worker
  purges what is older (``purge_expired``), content included.

**Digital signature.** A prescription is valid for pharmacies only once the
**appointment's professional** signs it — nobody else, not even the staff.
The professional signs with its **ICP-Brasil certificate** (an A1
``.pfx``/``.p12`` file and its password, sent with the request and used in
memory only: the private key is never stored); the system produces a
**CAdES-BES detached signature** (``.p7s``, see app.cms) over the exact
bytes of the file, embedding the certificate and its chain, and **verifies
it before accepting it**: the chain must reach a root of the configured
ICP-Brasil trust bundle (``ICP_BRASIL_TRUST_BUNDLE``), every certificate
must be valid at the signing time, and the certificate's CPF (ICP-Brasil
OID 2.16.76.1.3.1) must be the CPF on the professional's record. The
signature is re-verified on demand from the stored file and ``.p7s``
(``verify_document``), and a pharmacy can verify the same two files with
ITI's validator or OpenSSL. When the ICP-Brasil AD-RB policy is
configured (``ICP_BRASIL_POLICY_OID`` / ``_URL`` / ``_SHA256``) the
signature references it. Patients only see signed prescriptions.
"""

import hashlib
import hmac
import io
import os
import re
import secrets
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from sqlalchemy.orm import Session

from . import clock, cms
from .auth import SECRET_KEY
from .models import Appointment, AppointmentDocument, Professional

KIND_PRESCRIPTION = "prescription"
MAX_FILES = 5
MAX_BYTES = int(os.getenv("PRESCRIPTION_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
LINK_MAX_DAYS = 30
LINK_DAYS = max(1, min(LINK_MAX_DAYS, int(os.getenv("PRESCRIPTION_LINK_DAYS", str(LINK_MAX_DAYS)))))
RETENTION_YEARS = 10
STORAGE_DIR = Path(os.getenv("PRESCRIPTIONS_DIR", "./prescriptions"))
# ICP-Brasil: the trusted root CAs (PEM bundle published by ITI) and, optionally,
# the AD-RB signature policy the signatures reference.
TRUST_BUNDLE = Path(os.getenv("ICP_BRASIL_TRUST_BUNDLE", "./icp-brasil-roots.pem"))
POLICY_OID = os.getenv("ICP_BRASIL_POLICY_OID", "").strip()
POLICY_URL = os.getenv("ICP_BRASIL_POLICY_URL", "").strip()
POLICY_SHA256 = os.getenv("ICP_BRASIL_POLICY_SHA256", "").strip().lower()
SIGNATURE_EXTENSION = ".p7s"
SIGNATURE_CONTENT_TYPE = "application/pkcs7-signature"
MAX_CERTIFICATE_BYTES = 64 * 1024

# Accepted document types: kind -> (content type, extension)
TYPES = {
    "pdf": ("application/pdf", ".pdf"),
    "txt": ("text/plain; charset=utf-8", ".txt"),
    "rtf": ("application/rtf", ".rtf"),
    "doc": ("application/msword", ".doc"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "odt": ("application/vnd.oasis.opendocument.text", ".odt"),
}
ACCEPTED_DESCRIPTION = "PDF, plain text (.txt), RTF, Word (.doc, .docx) or OpenDocument text (.odt)"


class PrescriptionError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --- content checks ------------------------------------------------------------


def sniff(content: bytes) -> str:
    """The document kind of ``content`` (a key of ``TYPES``), decided from the
    bytes themselves. Raises ``PrescriptionError`` (415) for anything else:
    images, spreadsheets, executables, archives..."""
    if not content:
        raise PrescriptionError("The file is empty")
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"{\\rtf"):
        return "rtf"
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "doc"
    if content.startswith(b"PK\x03\x04"):
        return _sniff_zip(content)
    if b"\x00" not in content:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "txt"
    raise PrescriptionError(f"Unsupported file type: only text documents are accepted ({ACCEPTED_DESCRIPTION})", 415)


def _sniff_zip(content: bytes) -> str:
    """Office documents are zip containers: Word keeps ``word/document.xml``,
    OpenDocument declares its type in the ``mimetype`` entry."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "mimetype" in names:
                declared = archive.read("mimetype").decode("ascii", errors="replace").strip()
                if declared == TYPES["odt"][0]:
                    return "odt"
            if "word/document.xml" in names:
                return "docx"
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        pass
    raise PrescriptionError(f"Unsupported file type: only text documents are accepted ({ACCEPTED_DESCRIPTION})", 415)


def safe_filename(original: str, kind: str) -> str:
    """The name shown to users and sent on download: the original base name
    without path or control characters, with the extension of the detected
    type (whatever the upload claimed)."""
    base = Path((original or "").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f\"]", "", base).strip()
    stem = base.rsplit(".", 1)[0].strip() if "." in base else base
    stem = stem.strip(" .") or "prescription"
    return stem[:120] + TYPES[kind][1]


def check_count(row: Appointment, adding: int = 1) -> None:
    current = sum(1 for item in row.documents if item.kind == KIND_PRESCRIPTION)
    if current + adding > MAX_FILES:
        raise PrescriptionError(f"An appointment takes at most {MAX_FILES} prescription files", 409)


# --- storage -------------------------------------------------------------------


def store(content: bytes, kind: str) -> str:
    """Write the content under a random name; returns the storage key.
    ``kind`` is a document kind (``TYPES``) or an extension such as ``.p7s``."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    extension = TYPES[kind][1] if kind in TYPES else kind
    key = f"{secrets.token_urlsafe(24)}{extension}"
    (STORAGE_DIR / key).write_bytes(content)
    return key


def read(key: str) -> bytes | None:
    path = STORAGE_DIR / key
    if not path.is_file():
        return None
    return path.read_bytes()


def remove(key: str) -> None:
    try:
        (STORAGE_DIR / key).unlink()
    except FileNotFoundError:
        pass


# --- domain operations -----------------------------------------------------------


def attach(db: Session, row: Appointment, content: bytes, filename: str, user_id: int | None, note: str = "") -> AppointmentDocument:
    """Validate and attach a prescription to the appointment."""
    if row.status == "cancelada":
        raise PrescriptionError("Cancelled appointment: prescriptions cannot be attached", 409)
    if len(content) > MAX_BYTES:
        raise PrescriptionError(f"The file exceeds the limit of {MAX_BYTES // (1024 * 1024)} MB", 413)
    kind = sniff(content)
    check_count(row)
    document = AppointmentDocument(
        appointment_id=row.id,
        kind=KIND_PRESCRIPTION,
        filename=safe_filename(filename, kind),
        content_type=TYPES[kind][0],
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=store(content, kind),
        note=(note or "").strip()[:200],
        uploaded_by_id=user_id,
    )
    db.add(document)
    row.documents.append(document)
    db.flush()
    return document


def delete_document(db: Session, document: AppointmentDocument, row: Appointment | None = None) -> None:
    """Remove one file: its content, then its row (through the appointment's
    collection when the appointment is loaded, so the ORM state stays
    consistent)."""
    remove(document.storage_key)
    if document.signature_key:
        remove(document.signature_key)
    if row is not None and document in row.documents:
        row.documents.remove(document)  # delete-orphan: the row goes on flush
    else:
        db.delete(document)


def purge_appointment(db: Session, row: Appointment) -> int:
    """Delete every document of the appointment (content and rows): on
    cancellation and before a hard delete."""
    documents = list(row.documents)
    for document in documents:
        delete_document(db, document, row)
    db.flush()
    return len(documents)


def retain_until(marked_at: datetime | date) -> date:
    """Last day the documentation of a visit is kept (legal retention)."""
    day = marked_at.date() if isinstance(marked_at, datetime) else marked_at
    try:
        return day.replace(year=day.year + RETENTION_YEARS)
    except ValueError:  # 29 February
        return day.replace(year=day.year + RETENTION_YEARS, day=28)


def purge_expired(db: Session, today: date | None = None) -> int:
    """Delete the documents of visits past the legal retention (content and
    rows). Idempotent; run by the worker on every pass."""
    today = today or clock.today()
    try:
        cutoff = today.replace(year=today.year - RETENTION_YEARS)
    except ValueError:  # 29 February
        cutoff = today.replace(year=today.year - RETENTION_YEARS, day=28)
    rows = (
        db.query(AppointmentDocument)
        .join(Appointment, Appointment.id == AppointmentDocument.appointment_id)
        .filter(Appointment.marked_at < datetime.combine(cutoff, datetime.min.time()))
        .all()
    )
    for document in rows:
        delete_document(db, document)
    db.commit()
    return len(rows)


# --- digital signature (ICP-Brasil) -----------------------------------------------------


def cpf_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def trusted_roots() -> list[x509.Certificate]:
    """The root CAs of the trust bundle ([] when it is not configured)."""
    if not TRUST_BUNDLE.is_file():
        return []
    try:
        return x509.load_pem_x509_certificates(TRUST_BUNDLE.read_bytes())
    except ValueError:
        return []


def signature_policy() -> cms.SignaturePolicy | None:
    if not (POLICY_OID and POLICY_URL and POLICY_SHA256):
        return None
    return cms.SignaturePolicy(POLICY_OID, bytes.fromhex(POLICY_SHA256), POLICY_URL)


def is_signed(document: AppointmentDocument) -> bool:
    return document.signed_at is not None and bool(document.signature_key)


def open_certificate(pfx: bytes, password: str):
    """The key, certificate and chain of an A1 (PKCS#12) file."""
    if not pfx:
        raise PrescriptionError("Send the certificate file (.pfx / .p12)")
    if len(pfx) > MAX_CERTIFICATE_BYTES:
        raise PrescriptionError("The certificate file is too large")
    try:
        key, certificate, extra = pkcs12.load_key_and_certificates(pfx, password.encode("utf-8") if password else None)
    except (ValueError, TypeError) as exc:
        raise PrescriptionError("Could not open the certificate: wrong password or not a PKCS#12 (.pfx/.p12) file") from exc
    if key is None or certificate is None:
        raise PrescriptionError("The certificate file carries no private key and certificate")
    return key, certificate, list(extra or [])


def assert_certificate_is_the_professional(certificate: x509.Certificate, professional: Professional) -> str:
    """The certificate's CPF (ICP-Brasil person otherName) must be the
    professional's CPF on record. Returns the CPF."""
    expected = cpf_digits(professional.document)
    if not expected:
        raise PrescriptionError("Register your CPF on your professional record before signing: the certificate must carry it", 409)
    found = cms.cpf_of(certificate)
    if not found:
        raise PrescriptionError("The certificate carries no CPF: an ICP-Brasil certificate of a natural person is required")
    if found != expected:
        raise PrescriptionError("The certificate belongs to another person: its CPF is not the one on your professional record", 403)
    return found


def sign_document(
    db: Session,
    row: Appointment,
    document: AppointmentDocument,
    professional: Professional,
    pfx: bytes,
    password: str,
    user_id: int,
) -> cms.Report:
    """Sign the prescription with the professional's ICP-Brasil certificate
    and keep the detached signature. The signature is verified before it is
    accepted; the private key is discarded with the request."""
    if row.status == "cancelada":
        raise PrescriptionError("Cancelled appointment: prescriptions cannot be signed", 409)
    if is_signed(document):
        raise PrescriptionError("This prescription is already signed", 409)
    roots = trusted_roots()
    if not roots:
        raise PrescriptionError("The ICP-Brasil trust bundle is not configured on the server (ICP_BRASIL_TRUST_BUNDLE)", 503)
    content = read(document.storage_key)
    if content is None or hashlib.sha256(content).hexdigest() != document.sha256:
        raise PrescriptionError("The file is no longer available or was altered; attach it again", 410)
    key, certificate, chain = open_certificate(pfx, password)
    cpf = assert_certificate_is_the_professional(certificate, professional)
    signing_time = clock.now().astimezone(timezone.utc)
    try:
        signature = cms.build_signed_data(content, key, certificate, chain, signing_time, signature_policy())
    except ValueError as exc:
        raise PrescriptionError(f"Unsupported certificate: {exc}") from exc
    finally:
        del key
    report = cms.verify_signed_data(signature, content, roots, signing_time)
    if not report.valid:
        raise PrescriptionError("The signature could not be verified: " + "; ".join(report.failed()))
    document.signature_key = store(signature, SIGNATURE_EXTENSION)
    document.signature_sha256 = hashlib.sha256(signature).hexdigest()
    document.signed_at = clock.now().replace(microsecond=0)
    document.signed_by_id = user_id
    document.signer_name = cms.common_name(certificate.subject)
    document.signer_document = cpf
    document.signer_certificate = certificate.public_bytes(Encoding.PEM).decode()
    document.certificate_issuer = cms.common_name(certificate.issuer)
    document.certificate_serial = format(certificate.serial_number, "x")
    document.certificate_not_after = certificate.not_valid_after_utc.replace(tzinfo=None)
    document.signature_policy = report.policy_oid
    db.flush()
    return report


def verify_document(document: AppointmentDocument) -> cms.Report:
    """Re-verify a stored prescription from its file and its ``.p7s``."""
    report = cms.Report()
    if not is_signed(document):
        report.add("signed", False, "the prescription is not signed")
        return report
    content = read(document.storage_key)
    signature = read(document.signature_key)
    if content is None or signature is None:
        report.add("stored-files", False, "the file or its signature is no longer available")
        return report
    if hashlib.sha256(signature).hexdigest() != document.signature_sha256:
        report.add("stored-files", False, "the stored signature was altered")
        return report
    report = cms.verify_signed_data(signature, content, trusted_roots())
    expected_file = hashlib.sha256(content).hexdigest() == document.sha256
    report.add("stored-files", expected_file, "the stored file matches its fingerprint" if expected_file else "the stored file was altered")
    signer_ok = report.signer is not None and cms.cpf_of(report.signer) == document.signer_document
    report.add("signer", signer_ok, f"CPF {document.signer_document} ({document.signer_name})" if signer_ok else "the signer is not the one recorded")
    report.valid = all(check.ok for check in report.checks)
    return report


def verify_detached(content: bytes, signature: bytes) -> cms.Report:
    """Verify any file + ``.p7s`` pair against the trust bundle (public check)."""
    try:
        return cms.verify_signed_data(signature, content, trusted_roots())
    except Exception as exc:  # noqa: BLE001 - a malformed signature must yield a report, not a 500
        report = cms.Report()
        report.add("structure", False, f"malformed signature: {exc}")
        return report


# --- signed download links ---------------------------------------------------------


@dataclass(frozen=True)
class Link:
    token: str
    expires_at: datetime


def _signature(document_id: int, sha256: str, expires_ts: int) -> str:
    message = f"prescription:{document_id}:{sha256}:{expires_ts}".encode()
    return hmac.new(SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def issue_link(document: AppointmentDocument, now: datetime | None = None) -> Link:
    """A token that downloads the file until ``LINK_DAYS`` from now (30 at most)."""
    now = now or clock.now()
    expires_at = (now + timedelta(days=LINK_DAYS)).replace(microsecond=0)
    expires_ts = int(expires_at.timestamp())
    return Link(f"{expires_ts}.{_signature(document.id, document.sha256, expires_ts)}", expires_at)


def verify_link(document: AppointmentDocument, token: str, now: datetime | None = None) -> bool:
    """Whether ``token`` is a valid, unexpired link for this very file."""
    now = now or clock.now()
    try:
        expires_text, signature = token.split(".", 1)
        expires_ts = int(expires_text)
    except (ValueError, AttributeError):
        return False
    if not hmac.compare_digest(signature, _signature(document.id, document.sha256, expires_ts)):
        return False
    return now <= datetime.fromtimestamp(expires_ts)

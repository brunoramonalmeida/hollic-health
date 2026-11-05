"""Medical prescriptions attached to appointments (see app.prescriptions).

``GET /appointments/{id}/prescriptions`` lists the files with a signed
download link each (valid 30 days at most); ``POST`` attaches one
(multipart ``file``, optional ``note``); ``DELETE …/{docId}`` removes one;
``GET …/{docId}/download?token=`` serves the content — the token is the
credential, so the link opens in the browser without the bearer; a bearer
of someone with access to the appointment works too.

Digital signature (ICP-Brasil): ``POST …/{docId}/sign`` (multipart
``certificate`` = the professional's A1 ``.pfx``, ``password``) — only the
appointment's professional; ``GET …/{docId}/signature`` re-verifies the
stored signature and reports every check; ``GET …/{docId}/signature.p7s``
downloads the detached signature (same token as the file). Patients only
see signed prescriptions. ``POST /prescriptions/verify`` (no login) checks
any file + ``.p7s`` pair for pharmacies.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..auth import APPOINTMENT_ROLES, bearer, ensure_appointment_access, get_current_user, is_client, is_professional, require_appointment_area
from ..cms import Report, common_name, cpf_of
from ..database import get_db
from ..models import Appointment, AppointmentDocument, Professional, User
from ..notifications import prescription_attached, prescription_signed
from ..prescriptions import (
    ACCEPTED_DESCRIPTION,
    KIND_PRESCRIPTION,
    LINK_DAYS,
    MAX_BYTES,
    MAX_CERTIFICATE_BYTES,
    MAX_FILES,
    SIGNATURE_CONTENT_TYPE,
    PrescriptionError,
    attach,
    cpf_digits,
    delete_document,
    is_signed,
    issue_link,
    read,
    retain_until,
    sign_document,
    trusted_roots,
    verify_detached,
    verify_document,
    verify_link,
)
from ..schemas import PrescriptionOut, PrescriptionsOut, PublicVerificationOut, SignatureCheckOut, SignatureReportOut

router = APIRouter(prefix="/appointments", tags=["prescriptions"])
public = APIRouter(prefix="/prescriptions", tags=["prescriptions"])

READ = [Depends(require_appointment_area)]


def _load(db: Session, item_id: int) -> Appointment:
    row = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.customer),
            joinedload(Appointment.dependent),
            joinedload(Appointment.professional),
            joinedload(Appointment.specialty),
            selectinload(Appointment.documents).joinedload(AppointmentDocument.uploaded_by),
        )
        .filter(Appointment.id == item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return row


def _may_write(user: User, row: Appointment) -> bool:
    """Who attaches and removes prescriptions: the staff and the appointment's
    professional. Patients only read."""
    if is_client(user):
        return False
    if is_professional(user):
        return user.professional_id == row.professional_id
    return True


def _may_sign(user: User, row: Appointment) -> bool:
    """Only the appointment's professional signs prescriptions."""
    return is_professional(user) and user.professional_id == row.professional_id


def _visible(document: AppointmentDocument, user: User) -> bool:
    """Patients only see signed prescriptions (the valid ones); the staff and
    the professional also see the drafts awaiting the signature."""
    return is_signed(document) or not is_client(user)


def _download_path(document: AppointmentDocument, token: str) -> str:
    return f"/appointments/{document.appointment_id}/prescriptions/{document.id}/download?token={token}"


def _signature_path(document: AppointmentDocument, token: str) -> str:
    return f"/appointments/{document.appointment_id}/prescriptions/{document.id}/signature.p7s?token={token}"


def _report_out(report: Report, model=SignatureReportOut, **extra):
    signer = report.signer
    return model(
        valid=report.valid,
        checks=[SignatureCheckOut(name=check.name, ok=check.ok, detail=check.detail) for check in report.checks],
        signer_name=common_name(signer.subject) if signer else "",
        signer_document=cpf_of(signer) if signer else "",
        certificate_issuer=common_name(signer.issuer) if signer else "",
        certificate_serial=format(signer.serial_number, "x") if signer else "",
        certificate_not_before=signer.not_valid_before_utc.replace(tzinfo=None) if signer else None,
        certificate_not_after=signer.not_valid_after_utc.replace(tzinfo=None) if signer else None,
        chain=[common_name(item.subject) for item in report.chain],
        signing_time=report.signing_time.replace(tzinfo=None) if report.signing_time else None,
        digest_sha256=report.digest_hex,
        algorithm=report.algorithm,
        policy_oid=report.policy_oid,
        trust_bundle_configured=bool(trusted_roots()),
        **extra,
    )


def _out(document: AppointmentDocument, row: Appointment, user: User, now) -> PrescriptionOut:
    link = issue_link(document, now)
    signed = is_signed(document)
    return PrescriptionOut(
        id=document.id,
        appointment_id=document.appointment_id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        note=document.note or "",
        uploaded_at=document.uploaded_at,
        uploaded_by_name=document.uploaded_by.name if document.uploaded_by else None,
        download_url=_download_path(document, link.token),
        link_expires_at=link.expires_at,
        retain_until=retain_until(row.marked_at),
        can_delete=_may_write(user, row),
        signed=signed,
        signed_at=document.signed_at,
        signer_name=document.signer_name or "",
        signer_document=document.signer_document or "",
        certificate_issuer=document.certificate_issuer or "",
        certificate_serial=document.certificate_serial or "",
        certificate_not_after=document.certificate_not_after,
        signature_policy=document.signature_policy or "",
        signature_url=_signature_path(document, link.token) if signed else "",
        can_sign=_may_sign(user, row) and not signed and row.status != "cancelada",
    )


def _listing(row: Appointment, user: User) -> PrescriptionsOut:
    now = clock.now()
    items = [document for document in row.documents if document.kind == KIND_PRESCRIPTION]
    return PrescriptionsOut(
        appointment_id=row.id,
        items=[_out(document, row, user, now) for document in items if _visible(document, user)],
        max_files=MAX_FILES,
        remaining=max(0, MAX_FILES - len(items)),
        can_attach=_may_write(user, row) and row.status != "cancelada",
        accepted=ACCEPTED_DESCRIPTION,
        max_bytes=MAX_BYTES,
        link_days=LINK_DAYS,
        signing_available=bool(trusted_roots()),
    )


@router.get("/{item_id}/prescriptions", response_model=PrescriptionsOut, dependencies=READ)
def list_prescriptions(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The prescription files of the appointment, each with a fresh signed
    download link (valid ``link_days``, 30 at most)."""
    row = _load(db, item_id)
    ensure_appointment_access(user, row)
    return _listing(row, user)


@router.post("/{item_id}/prescriptions", response_model=PrescriptionOut, status_code=201, dependencies=READ)
def attach_prescription(
    item_id: int,
    file: UploadFile = File(description="The document: PDF, plain text, RTF, Word or OpenDocument text"),
    note: str = Form(default="", max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Attach a prescription (the staff or the appointment's professional).
    The type is checked on the content; up to 5 files per appointment; never
    on a cancelled appointment."""
    row = _load(db, item_id)
    ensure_appointment_access(user, row)
    if not _may_write(user, row):
        raise HTTPException(status_code=403, detail="Only the staff or the appointment's professional attach prescriptions")
    content = file.file.read(MAX_BYTES + 1)
    try:
        document = attach(db, row, content, file.filename or "", user.id, note)
    except PrescriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    prescription_attached(db, row, document, user)
    db.commit()
    row = _load(db, item_id)
    saved = next(item for item in row.documents if item.id == document.id)
    return _out(saved, row, user, clock.now())


@router.delete("/{item_id}/prescriptions/{document_id}", status_code=204, dependencies=READ)
def delete_prescription(item_id: int, document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove a file (the staff or the appointment's professional); its links stop working at once."""
    row = _load(db, item_id)
    ensure_appointment_access(user, row)
    if not _may_write(user, row):
        raise HTTPException(status_code=403, detail="Only the staff or the appointment's professional remove prescriptions")
    document = next((item for item in row.documents if item.id == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    delete_document(db, document, row)
    db.commit()


@router.post("/{item_id}/prescriptions/{document_id}/sign", response_model=PrescriptionOut, dependencies=READ)
def sign_prescription(
    item_id: int,
    document_id: int,
    certificate: UploadFile = File(description="The professional's ICP-Brasil A1 certificate (.pfx / .p12)"),
    password: str = Form(default="", max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The appointment's professional signs the prescription with its
    ICP-Brasil certificate: a CAdES detached signature is produced, verified
    (chain to the trust bundle, validity, the certificate's CPF against the
    professional's record) and stored. The private key is used for this
    request only and never kept."""
    row = _load(db, item_id)
    ensure_appointment_access(user, row)
    if not _may_sign(user, row):
        raise HTTPException(status_code=403, detail="Only the appointment's professional signs its prescriptions")
    document = next((item for item in row.documents if item.id == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    professional = db.get(Professional, row.professional_id)
    pfx = certificate.file.read(MAX_CERTIFICATE_BYTES + 1)
    try:
        sign_document(db, row, document, professional, pfx, password, user.id)
    except PrescriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    prescription_signed(db, row, document, user)
    db.commit()
    row = _load(db, item_id)
    saved = next(item for item in row.documents if item.id == document_id)
    return _out(saved, row, user, clock.now())


@router.get("/{item_id}/prescriptions/{document_id}/signature", response_model=SignatureReportOut, dependencies=READ)
def prescription_signature(item_id: int, document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Re-verify the stored signature (file + .p7s) now and report every check."""
    row = _load(db, item_id)
    ensure_appointment_access(user, row)
    document = next((item for item in row.documents if item.id == document_id and _visible(item, user)), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return _report_out(verify_document(document))


def _authorize_download(db: Session, document: AppointmentDocument, item_id: int, token: str | None, credentials) -> None:
    """The signed link, or the bearer of someone who may read the appointment
    (a patient only for signed prescriptions)."""
    now = clock.now()
    if token and verify_link(document, token, now):
        return
    if credentials is None:
        detail = "This download link has expired; open the appointment for a new one" if token else "Not authenticated"
        raise HTTPException(status_code=401 if not token else 403, detail=detail)
    user = get_current_user(credentials, db)
    if user.role not in APPOINTMENT_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed for your role")
    ensure_appointment_access(user, db.get(Appointment, item_id))
    if not _visible(document, user):
        raise HTTPException(status_code=403, detail="This prescription is not signed yet")


def _file_response(content: bytes, filename: str, media_type: str) -> Response:
    ascii_name = filename.encode("ascii", errors="replace").decode().replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{item_id}/prescriptions/{document_id}/download")
def download_prescription(
    item_id: int,
    document_id: int,
    token: str | None = Query(default=None, description="Signed link issued by the listing (valid 30 days at most)"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    """The file itself. Opened with the signed ``token`` (no login needed
    until it expires) or with the bearer of someone who may read the
    appointment."""
    document = db.get(AppointmentDocument, document_id)
    if document is None or document.appointment_id != item_id:
        raise HTTPException(status_code=404, detail="Prescription not found")
    _authorize_download(db, document, item_id, token, credentials)
    content = read(document.storage_key)
    if content is None:
        raise HTTPException(status_code=410, detail="The file is no longer available")
    return _file_response(content, document.filename, document.content_type)


@router.get("/{item_id}/prescriptions/{document_id}/signature.p7s")
def download_signature(
    item_id: int,
    document_id: int,
    token: str | None = Query(default=None, description="The same signed link as the file"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    """The detached CAdES signature (.p7s) of a signed prescription: with the
    file, it can be verified outside the system (ITI's validator, OpenSSL)."""
    document = db.get(AppointmentDocument, document_id)
    if document is None or document.appointment_id != item_id:
        raise HTTPException(status_code=404, detail="Prescription not found")
    if not is_signed(document):
        raise HTTPException(status_code=404, detail="This prescription is not signed")
    _authorize_download(db, document, item_id, token, credentials)
    signature = read(document.signature_key)
    if signature is None:
        raise HTTPException(status_code=410, detail="The signature is no longer available")
    return _file_response(signature, document.filename.rsplit(".", 1)[0] + ".p7s", SIGNATURE_CONTENT_TYPE)


@public.post("/verify", response_model=PublicVerificationOut)
def verify_prescription(
    file: UploadFile = File(description="The prescription file"),
    signature: UploadFile = File(description="Its detached signature (.p7s)"),
    db: Session = Depends(get_db),
):
    """Public check for pharmacies: verify a prescription file and its .p7s
    against the ICP-Brasil trust bundle and say whether the signer is a
    professional of the network. No login; nothing is stored."""
    content = file.file.read(MAX_BYTES + 1)
    p7s = signature.file.read(MAX_CERTIFICATE_BYTES * 4)
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is too large")
    report = verify_detached(content, p7s)
    professional = None
    if report.signer is not None:
        cpf = cpf_of(report.signer)
        if cpf:
            professional = next((item for item in db.query(Professional).all() if cpf_digits(item.document) == cpf), None)
    return _report_out(
        report,
        PublicVerificationOut,
        professional_registered=professional is not None,
        professional_name=professional.name if professional else None,
        professional_specialty=professional.specialty.name if professional else None,
    )

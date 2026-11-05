"""The professional's signature.

An appointment is **completed** (status ``realizada``) only when the
professional who attended it signs it. Nobody else — reception, supervisor,
administrator or patient — can mark an appointment as completed: the
check-out records that the visit ended and leaves it *awaiting signature*.
Only signed appointments count as completed in the reports and pay the
professional.

Rules:

* only a ``professional`` login tied to the appointment's professional signs;
* the appointment must have been **started** with the patient's virtual card
  code (``app.virtual_card``): the signature attests a visit of a verified
  patient;
* cancelled appointments cannot be signed; signing twice is refused;
* an appointment cannot be signed before its slot starts (a signature
  attests a visit that happened);
* the professional may revoke its own signature (mistake), which returns the
  appointment to ``agendada``; the staff may still cancel a signed
  appointment, which drops the signature;
* on a signed appointment nothing else changes: patient, professional and
  time are frozen until the signature is revoked.
"""

from datetime import datetime

STATUS_SCHEDULED = "agendada"
STATUS_COMPLETED = "realizada"
STATUS_CANCELLED = "cancelada"

SIGNATURE_REQUIRED = "Only the professional completes an appointment, by signing it"
SIGNED_FROZEN = "Signed appointment: revoke the signature before changing it"
START_REQUIRED = "Start the appointment with the patient's card code before signing it"


class SignatureError(Exception):
    status_code = 409

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def is_signed(row) -> bool:
    return row.signed_at is not None


def owns_appointment(user, row) -> bool:
    """The professional login of the appointment's professional."""
    return user.role == "professional" and user.professional_id is not None and user.professional_id == row.professional_id


def can_sign(row, user, now: datetime) -> bool:
    try:
        assert_can_sign(row, user, now)
    except SignatureError:
        return False
    return True


def can_revoke(row, user) -> bool:
    try:
        assert_can_revoke(row, user)
    except SignatureError:
        return False
    return True


def assert_can_sign(row, user, now: datetime) -> None:
    if not owns_appointment(user, row):
        raise SignatureError("Only the appointment's professional can sign it", 403)
    if row.status == STATUS_CANCELLED:
        raise SignatureError("Cancelled appointments cannot be signed")
    if is_signed(row) or row.status == STATUS_COMPLETED:
        raise SignatureError(f"Already signed at {row.signed_at:%d/%m/%Y %H:%M}" if row.signed_at else "Already completed")
    if now < row.marked_at:
        raise SignatureError(f"The appointment starts at {row.marked_at:%d/%m/%Y %H:%M}; sign it after the visit")
    if getattr(row, "started_at", None) is None:
        raise SignatureError(START_REQUIRED)


def assert_can_revoke(row, user) -> None:
    if not owns_appointment(user, row):
        raise SignatureError("Only the appointment's professional can revoke the signature", 403)
    if not is_signed(row):
        raise SignatureError("Appointment is not signed")


def sign(row, user, now: datetime) -> None:
    assert_can_sign(row, user, now)
    row.signed_at = now.replace(microsecond=0)
    row.signed_by_id = user.id
    row.status = STATUS_COMPLETED


def revoke(row, user) -> None:
    assert_can_revoke(row, user)
    row.signed_at = None
    row.signed_by_id = None
    row.status = STATUS_SCHEDULED


def clear_signature(row) -> None:
    """When the staff cancels a signed appointment the signature is dropped."""
    row.signed_at = None
    row.signed_by_id = None


def awaiting_signature(row, now: datetime) -> bool:
    """A scheduled appointment whose slot already started and that the
    professional has not signed yet (no-shows included: the professional
    decides, by not signing, that it was not attended)."""
    return row.status == STATUS_SCHEDULED and not is_signed(row) and row.marked_at <= now


def check_status_change(row, new_status: str) -> None:
    """Rules for the status field of a create/edit body.

    ``row`` is ``None`` on create. Raises ``SignatureError`` (403) when the
    body tries to complete the appointment without a signature."""
    if new_status != STATUS_COMPLETED:
        return
    if row is None or not is_signed(row):
        raise SignatureError(SIGNATURE_REQUIRED, 403)


def check_signed_edit(row, body) -> None:
    """On a signed appointment only the notes may change, or the status may
    go to cancelled (which drops the signature)."""
    if not is_signed(row):
        return
    if body.status == STATUS_SCHEDULED:
        raise SignatureError(SIGNED_FROZEN)
    if (
        row.customer_id != body.customer_id
        or row.dependent_id != body.dependent_id
        or row.professional_id != body.professional_id
        or row.marked_at != body.marked_at
        or (getattr(body, "urgent", None) is not None and bool(getattr(row, "urgent", False)) != bool(body.urgent))
        or (getattr(body, "remote", None) is not None and bool(getattr(row, "remote", False)) != bool(body.remote))
    ):
        raise SignatureError(SIGNED_FROZEN)

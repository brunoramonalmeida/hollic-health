"""The patient's virtual card: a rotating code that proves who is there.

Every patient (the holder and each dependent) has a secret stored only on
the server. From it the card shows a **6-digit code that changes every 30
seconds**, computed as a TOTP (RFC 6238: HMAC-SHA1 over the 30-second time
step, RFC 4226 dynamic truncation). Only the patient's own login can read the
card; to **start** an appointment the reception or the professional types the
code the patient shows, and the server checks it against the secret of the
appointment's patient.

Validation accepts the **current code and the previous one** (one step of
tolerance backwards, so a code read at second 29 is still good a moment
later), never a future one. Each code is compared in constant time.

Brute force: a 6-digit code with two valid values gives 2 chances in a
million per try, so after ``MAX_FAILURES`` wrong codes the appointment is
locked for ``LOCK_MINUTES`` and the patient and the staff are alerted.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timedelta, timezone

PERIOD_SECONDS = 30
DIGITS = 6
PREVIOUS_STEPS_ACCEPTED = 1  # current + previous code
MAX_FAILURES = 5
LOCK_MINUTES = 5
SECRET_BYTES = 20


class CardError(Exception):
    status_code = 409

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def generate_secret() -> str:
    """A new base32 secret (160 bits, as recommended for HMAC-SHA1)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def time_step(moment: datetime, period: int = PERIOD_SECONDS) -> int:
    """Number of whole periods since the Unix epoch (naive datetimes are UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) // period


def code_for_step(secret: str, step: int, digits: int = DIGITS) -> str:
    """HOTP (RFC 4226) value for one counter, zero-padded to ``digits``."""
    key = base64.b32decode(secret.upper().replace(" ", ""), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def current_code(secret: str, now: datetime, period: int = PERIOD_SECONDS) -> str:
    return code_for_step(secret, time_step(now, period))


def seconds_remaining(now: datetime, period: int = PERIOD_SECONDS) -> int:
    """Seconds until the current code changes (1..period)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return period - int(now.timestamp()) % period


def normalize_code(text: str) -> str:
    """What the reception types: digits only ("123 456" -> "123456")."""
    return "".join(ch for ch in (text or "") if ch.isdigit())


def verify_code(secret: str, code: str, now: datetime, *, previous: int = PREVIOUS_STEPS_ACCEPTED) -> bool:
    """True when ``code`` is the current code or one of the ``previous`` ones.
    Future codes are never accepted; comparisons are constant-time."""
    typed = normalize_code(code)
    if len(typed) != DIGITS:
        return False
    step = time_step(now)
    matched = False
    for back in range(previous + 1):
        # Compare every candidate (no early return) so timing reveals nothing.
        if hmac.compare_digest(code_for_step(secret, step - back), typed):
            matched = True
    return matched


def card_number(customer_id: int, dependent_id: int | None) -> str:
    """Printed on the card: stable, not secret (holder: SS 000123 00; dependent: SS 000123 <id>)."""
    return f"SS {customer_id:06d} {dependent_id or 0:02d}"


# --- start of the appointment ---------------------------------------------------------


def is_locked(row, now: datetime) -> bool:
    return row.start_locked_until is not None and now < row.start_locked_until


def assert_can_start(row, now: datetime, opens_before: timedelta) -> None:
    """Whether the appointment may be started now (before checking the code)."""
    if row.status == "cancelada":
        raise CardError("Appointment is cancelled")
    if row.status == "realizada" or row.signed_at is not None:
        raise CardError("Appointment is already completed")
    if row.started_at is not None:
        raise CardError(f"Already started at {row.started_at:%d/%m/%Y %H:%M}")
    if now < row.marked_at - opens_before:
        raise CardError(f"The appointment can be started from {(row.marked_at - opens_before):%d/%m/%Y %H:%M}")
    if is_locked(row, now):
        raise CardError(f"Too many wrong codes; locked until {row.start_locked_until:%H:%M}", 429)


def can_start(row, now: datetime, opens_before: timedelta) -> bool:
    try:
        assert_can_start(row, now, opens_before)
    except CardError:
        return False
    return True


def register_failure(row, now: datetime) -> bool:
    """Count a wrong code; returns True when this failure locked the appointment."""
    row.start_failures = (row.start_failures or 0) + 1
    if row.start_failures >= MAX_FAILURES:
        row.start_locked_until = now + timedelta(minutes=LOCK_MINUTES)
        row.start_failures = 0
        return True
    return False


def start(row, user, now: datetime) -> None:
    """Mark the appointment as started by ``user`` (the code was already verified).
    Starting proves the patient is there: it also registers the arrival when
    no check-in was done."""
    stamp = now.replace(microsecond=0)
    row.started_at = stamp
    row.started_by_id = user.id
    row.start_failures = 0
    row.start_locked_until = None
    if row.checked_in_at is None:
        row.checked_in_at = stamp
        row.checked_in_by_id = user.id


def undo_start(row) -> None:
    row.started_at = None
    row.started_by_id = None

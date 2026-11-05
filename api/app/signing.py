"""Strong signature for professionals: password + authenticator code + drawn signature.

Whatever a professional signs — an appointment (the visit happened) or a
receipt (the money was received) — requires, in one request:

* the **account password** typed again (proves the person at the keyboard
  knows it, not just whoever holds an open session);
* a **TOTP code** from the professional's authenticator app (RFC 6238, 30 s,
  6 digits; the current and the previous code are accepted), enrolled once
  through ``/auth/totp/setup`` + ``/auth/totp/confirm``;
* a **hand-drawn signature** (JPEG data URL produced by the signature pad),
  stored with the signed record and printed on the receipt.

Wrong password or code attempts are counted per user; after ``MAX_FAILURES``
the account cannot sign for ``LOCK_MINUTES``. Revoking a signature requires
the password and the code (no drawing).
"""

import base64
from datetime import datetime, timedelta

from .auth import verify_password
from .pdf import jpeg_info
from .virtual_card import current_code, generate_secret, verify_code

MAX_FAILURES = 5
LOCK_MINUTES = 5
MAX_IMAGE_BYTES = 300_000
ISSUER = "Sante Saude"


class SigningError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --- authenticator enrolment -------------------------------------------------------------


def totp_enabled(user) -> bool:
    return bool(user.totp_secret) and user.totp_enabled_at is not None


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """What authenticator apps read (shown as text when no QR is available)."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def begin_enrolment(user) -> str:
    """Issue a new secret; it only counts once a code from it was confirmed."""
    user.totp_secret = generate_secret()
    user.totp_enabled_at = None
    return user.totp_secret


def confirm_enrolment(user, code: str, now: datetime) -> None:
    if not user.totp_secret:
        raise SigningError("Start the authenticator setup first", 409)
    if totp_enabled(user):
        raise SigningError("Authenticator already confirmed", 409)
    if not verify_code(user.totp_secret, code, now):
        raise SigningError("The code does not match; check the authenticator app and the clock")
    user.totp_enabled_at = now.replace(microsecond=0)


def reset_enrolment(user) -> None:
    user.totp_secret = None
    user.totp_enabled_at = None


# --- the signature itself ------------------------------------------------------------------


def is_locked(user, now: datetime) -> bool:
    return user.sign_locked_until is not None and now < user.sign_locked_until


def _register_failure(user, now: datetime) -> None:
    user.sign_failures = (user.sign_failures or 0) + 1
    if user.sign_failures >= MAX_FAILURES:
        user.sign_locked_until = now + timedelta(minutes=LOCK_MINUTES)
        user.sign_failures = 0


def verify_credentials(user, password: str, code: str, now: datetime) -> None:
    """Password + authenticator code. Counts failures and locks; raises SigningError."""
    if is_locked(user, now):
        raise SigningError(f"Too many failed attempts; signing is locked until {user.sign_locked_until:%H:%M}", 429)
    if not totp_enabled(user):
        raise SigningError("Set up your authenticator app before signing", 403)
    if not password or not verify_password(password, user.password_hash):
        _register_failure(user, now)
        raise SigningError("Wrong password", 401)
    if not verify_code(user.totp_secret, code or "", now):
        _register_failure(user, now)
        raise SigningError("Wrong authenticator code", 401)
    user.sign_failures = 0
    user.sign_locked_until = None


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) of a JPEG; raises ValueError when it is not one."""
    width, height, _ = jpeg_info(data)
    return width, height


def validate_signature_image(data_url: str) -> bytes:
    """The drawing from the signature pad: a JPEG data URL, decoded and checked."""
    prefix = "data:image/jpeg;base64,"
    if not data_url or not data_url.startswith(prefix):
        raise SigningError("The signature drawing is required (JPEG image)")
    try:
        data = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (ValueError, TypeError):
        raise SigningError("The signature drawing is not a valid image")
    if len(data) > MAX_IMAGE_BYTES:
        raise SigningError("The signature drawing is too large")
    try:
        width, height = jpeg_size(data)
    except ValueError:
        raise SigningError("The signature drawing is not a valid JPEG image")
    if width < 50 or height < 20:
        raise SigningError("The signature drawing is too small")
    return data


def demo_code(user, now: datetime) -> str:
    """Current code of a user's authenticator (tests and seed)."""
    return current_code(user.totp_secret, now)

"""Remote appointments (video call).

A professional **offers** remote appointments when it says so in its own
profile (``Professional.remote_enabled``, also editable on the professional
form). A remote appointment is booked like an in-person one — a slot of the
regular working hours, with the same time-off, conflict, capacity and plan
rules (see ``app.scheduling``) — and is priced with the remote price list
(see ``app.pricing``). In person remains the default: an appointment is
remote only when booked with ``remote: true``.

Every remote appointment has a **meeting link** (``Appointment.meeting_url``)
the patient and the professional join at the slot time. The link is issued
when the appointment is booked as remote — a unique room under
``REMOTE_MEETING_BASE_URL`` (default: the public Jitsi Meet service, which
needs no account) — unless the staff supplies its own link (the
professional's Meet/Zoom room, for instance). Switching an appointment back
to in person drops the link; moving a remote appointment keeps it.
"""

import os
import secrets

# Base of the generated meeting links; a unique room name is appended.
MEETING_BASE_URL = os.getenv("REMOTE_MEETING_BASE_URL", "https://meet.jit.si").rstrip("/")
ROOM_PREFIX = "HollicHealth"
MAX_URL_LENGTH = 500


def new_room_name() -> str:
    """An unguessable room name (128 bits of randomness)."""
    return f"{ROOM_PREFIX}-{secrets.token_urlsafe(16)}"


def new_meeting_url() -> str:
    return f"{MEETING_BASE_URL}/{new_room_name()}"


def clean_meeting_url(value: str | None) -> str:
    """A link supplied by the staff: trimmed; empty means "issue one"."""
    value = (value or "").strip()
    if not value:
        return ""
    if not (value.startswith("https://") or value.startswith("http://")):
        raise ValueError("meeting_url must start with https:// or http://")
    if len(value) > MAX_URL_LENGTH:
        raise ValueError(f"meeting_url is limited to {MAX_URL_LENGTH} characters")
    return value


def apply_meeting_link(row, remote: bool, supplied: str | None = None) -> None:
    """Keep ``row.meeting_url`` consistent with the kind of appointment: a
    remote one has a link (the supplied one, the existing one, or a new
    room); an in-person one has none."""
    if not remote:
        row.meeting_url = ""
        return
    link = clean_meeting_url(supplied)
    if link:
        row.meeting_url = link
    elif not row.meeting_url:
        row.meeting_url = new_meeting_url()

"""Email backends.

``MAIL_BACKEND`` selects the sender: ``console`` (default, logs the message),
``file`` (writes ``.eml`` files into ``MAIL_OUTBOX_DIR``), ``smtp`` (real
delivery using ``SMTP_*`` variables) or ``memory`` (tests). Senders raise on
failure; the queue worker turns that into a retry.
"""

import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

log = logging.getLogger("sante.mail")

MAIL_FROM = os.getenv("MAIL_FROM", "Santé Saúde <no-reply@sante-saude.example>")


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    message.set_content(body)
    return message


class ConsoleSender:
    def send(self, to: str, subject: str, body: str) -> None:
        log.info("email to=%s subject=%r\n%s", to, subject, body)


class FileSender:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def send(self, to: str, subject: str, body: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe_to = "".join(ch if ch.isalnum() else "_" for ch in to)
        path = self.directory / f"{stamp}_{safe_to}.eml"
        path.write_bytes(bytes(build_message(to, subject, body)))


class SMTPSender:
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "localhost")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.use_tls = os.getenv("SMTP_TLS", "1") not in {"0", "false", "False"}

    def send(self, to: str, subject: str, body: str) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(build_message(to, subject, body))


@dataclass
class MemorySender:
    """Test double. ``fail_times`` makes the first N sends raise."""

    sent: list[dict] = field(default_factory=list)
    fail_times: int = 0
    failures: int = 0

    def send(self, to: str, subject: str, body: str) -> None:
        if self.failures < self.fail_times:
            self.failures += 1
            raise ConnectionError("SMTP unavailable (simulated)")
        self.sent.append({"to": to, "subject": subject, "body": body})


def sender_from_env() -> EmailSender:
    backend = os.getenv("MAIL_BACKEND", "console").lower()
    if backend == "smtp":
        return SMTPSender()
    if backend == "file":
        return FileSender(os.getenv("MAIL_OUTBOX_DIR", "./outbox"))
    if backend == "memory":
        return MemorySender()
    return ConsoleSender()


_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    global _sender
    if _sender is None:
        _sender = sender_from_env()
    return _sender


def set_sender(sender: EmailSender | None) -> None:
    """Replace the process-wide sender (tests)."""
    global _sender
    _sender = sender

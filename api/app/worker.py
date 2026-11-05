"""Background worker: schedules reminders and the monthly payout closing,
relays outbox jobs to the broker and consumes them.

Runs inside the API process (``WORKER_ENABLED=1``, default) every
``WORKER_INTERVAL_SECONDS``, or as a separate process (``python -m
app.worker``, with ``WORKER_ENABLED=0`` in the API) — with RabbitMQ several
worker processes can consume the same queue. ``tick()`` is also exposed to
administrators (``POST /queue/run``) and used directly by the tests.

Job kinds: ``email`` (queued notifications), ``payout.close`` (fan out one
settlement job per payee for a monthly closing) and ``payout.settle`` (create
one payee's settlement, idempotent). Every pass also purges the prescription
files and chat attachments past their legal retention (see
app.prescriptions, app.chat) and the chat blobs uploaded but never sent, and
checks whether the corporate agreements still meet their minimum of
employees today (see app.partners.watch_agreements).
"""

import asyncio
import logging
import os
from datetime import datetime

from . import clock
from .broker import get_broker
from .closing import KIND_CLOSE, KIND_SETTLE, handle_close, handle_settle, schedule_monthly_closing
from .database import SessionLocal
from .mailer import get_sender
from .partners import watch_agreements
from .payout_service import create_settlement
from .chat import purge_expired as purge_expired_chat
from .prescriptions import purge_expired as purge_expired_prescriptions, retain_until
from .queue import KIND_EMAIL, process_due_jobs
from .reminders import schedule_reminders

log = logging.getLogger("sante.worker")

WORKER_ENABLED = os.getenv("WORKER_ENABLED", "1") not in {"0", "false", "False"}
WORKER_INTERVAL_SECONDS = int(os.getenv("WORKER_INTERVAL_SECONDS", "30"))


def send_email(payload: dict) -> None:
    get_sender().send(payload["to"], payload["subject"], payload["body"])


def handlers_for(db) -> dict:
    """Job handlers bound to the session of this pass."""
    return {
        KIND_EMAIL: send_email,
        KIND_CLOSE: lambda payload: handle_close(db, payload),
        KIND_SETTLE: lambda payload: handle_settle(db, payload, create_settlement),
    }


def tick(db, now: datetime | None = None) -> dict:
    """One pass: due reminders, the monthly closing when its day came, the
    prescriptions past their legal retention, then relay due jobs to the
    broker and consume what is waiting."""
    reminders = schedule_reminders(db, now)
    closing = schedule_monthly_closing(db, now.date() if now else None)
    purged = purge_expired_prescriptions(db, now.date() if now else None)
    chat = purge_expired_chat(db, now.date() if now else None, retain_until)
    agreements = watch_agreements(db, now.date() if now else clock.today())
    queue = process_due_jobs(db, handlers_for(db), broker=get_broker())  # real clock, like the jobs' run_at
    return {
        "reminders": reminders,
        "closing": {"scheduled": 1 if closing is not None else 0},
        "prescriptions": {"purged": purged},
        "chat": chat,
        "agreements": {"changed": agreements},
        "queue": queue,
    }


def run_once() -> dict:
    db = SessionLocal()
    try:
        return tick(db)
    finally:
        db.close()


async def loop() -> None:
    log.info("worker started (every %ss)", WORKER_INTERVAL_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(run_once)
            if result["queue"]["delivered"] or result["queue"]["failed"] or any(result["reminders"].values()) or result["prescriptions"]["purged"]:
                log.info("worker tick: %s", result)
        except Exception:  # noqa: BLE001 - the loop must survive any failure
            log.exception("worker tick failed")
        await asyncio.sleep(WORKER_INTERVAL_SECONDS)


if __name__ == "__main__":  # standalone worker process
    logging.basicConfig(level=logging.INFO)
    asyncio.run(loop())

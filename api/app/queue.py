"""Outbox queue.

Jobs are rows written in the same transaction as the business event. The
worker **relays** pending jobs whose ``run_at`` has passed to the message
broker (``app.broker``: RabbitMQ, or in memory) — the row becomes
``queued`` only after the broker confirmed the message — and **consumers**
take the messages, run the handler for the job's ``kind`` and acknowledge.
On failure the job goes back to ``pending`` with an exponential back-off
(1, 2, 4, 8... minutes, capped) and is relayed again when due. After
``max_attempts`` the job is ``dead`` and can be re-queued by an administrator.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import clock
from .broker import Broker, PublishError
from .models import QueueJob

log = logging.getLogger("sante.queue")

PENDING = "pending"
QUEUED = "queued"  # published to the broker, waiting for a consumer
PROCESSING = "processing"
DONE = "done"
DEAD = "dead"
STATUSES = (PENDING, QUEUED, PROCESSING, DONE, DEAD)

KIND_EMAIL = "email"
DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_CAP = timedelta(hours=6)
# A job stuck in "processing" longer than this (worker crashed mid-send) is retried.
STALE_PROCESSING = timedelta(minutes=15)
# A job "queued" for longer than this without a consumer taking it (message lost, broker
# purged) is published again.
STALE_QUEUED = timedelta(minutes=30)

Handler = Callable[[dict], None]


def utcnow() -> datetime:
    """Timestamp of the queue (application clock, like every other stamp)."""
    return clock.now().replace(microsecond=0)


def backoff(attempts: int) -> timedelta:
    """Delay before the next try after ``attempts`` failures."""
    delay = BACKOFF_BASE * (2 ** max(attempts - 1, 0))
    return min(delay, BACKOFF_CAP)


def enqueue(
    db: Session,
    kind: str,
    payload: dict,
    *,
    event: str = "",
    run_at: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> QueueJob:
    job = QueueJob(
        kind=kind,
        payload=json.dumps(payload, ensure_ascii=False),
        status=PENDING,
        run_at=run_at or utcnow(),
        max_attempts=max_attempts,
        event=event,
    )
    db.add(job)
    return job


def due_jobs(db: Session, now: datetime, limit: int) -> list[QueueJob]:
    """Jobs to relay now: pending and due, or stuck in processing/queued."""
    stale = now - STALE_PROCESSING
    stale_queued = now - STALE_QUEUED
    return (
        db.query(QueueJob)
        .filter(
            ((QueueJob.status == PENDING) & (QueueJob.run_at <= now))
            | ((QueueJob.status == PROCESSING) & (QueueJob.run_at <= stale))
            | ((QueueJob.status == QUEUED) & (QueueJob.run_at <= stale_queued))
        )
        .order_by(QueueJob.run_at, QueueJob.id)
        .limit(limit)
        .all()
    )


def relay_due_jobs(db: Session, broker: Broker, now: datetime, limit: int = 100) -> int:
    """Publish due jobs to the broker; a row is ``queued`` only once confirmed."""
    published = 0
    for job in due_jobs(db, now, limit):
        try:
            broker.publish(
                {"job_id": job.id, "kind": job.kind, "attempt": job.attempts + 1},
                message_id=f"job-{job.id}-{job.attempts + 1}",
                headers={"kind": job.kind, "event": job.event or ""},
            )
        except PublishError as exc:
            log.warning("broker did not confirm job %s: %s", job.id, exc)
            break  # the broker is unavailable: leave everything pending for the next tick
        job.status = QUEUED
        job.run_at = now
        db.commit()
        published += 1
    return published


def run_job(db: Session, job: QueueJob, handlers: dict[str, Handler], now: datetime) -> bool:
    """Execute one job and record the outcome. Returns True when delivered."""
    job.status = PROCESSING
    job.run_at = now
    db.commit()
    try:
        handler = handlers.get(job.kind)
        if handler is None:
            raise LookupError(f"No handler for job kind {job.kind!r}")
        handler(json.loads(job.payload))
    except Exception as exc:  # noqa: BLE001 - any failure is a retry
        job.attempts += 1
        job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        if job.attempts >= job.max_attempts:
            job.status = DEAD
            job.finished_at = now
        else:
            job.status = PENDING
            job.run_at = now + backoff(job.attempts)
        db.commit()
        return False
    job.status = DONE
    job.attempts += 1
    job.last_error = ""
    job.finished_at = now
    db.commit()
    return True


def consume_jobs(db: Session, broker: Broker, handlers: dict[str, Handler], now: datetime | None = None, limit: int = 100) -> dict:
    """Take messages from the broker and run the jobs they point to.

    The handler returns True to ack (the job ran, whatever the outcome, which
    is recorded on the row) and False to dead-letter a message that does not
    point to a job."""
    now = now or utcnow()
    outcome = {"delivered": 0, "failed": 0, "skipped": 0, "dead_lettered": 0}

    def handle(message: dict) -> bool:
        job_id = message.get("job_id") if isinstance(message, dict) else None
        job = db.get(QueueJob, job_id) if isinstance(job_id, int) else None
        if job is None:
            outcome["dead_lettered"] += 1
            return False
        if job.status in (DONE, DEAD) or (job.status == PENDING and job.run_at > now):
            outcome["skipped"] += 1  # duplicate delivery, or already rescheduled for later
            return True
        if run_job(db, job, handlers, now):
            outcome["delivered"] += 1
        else:
            outcome["failed"] += 1
        return True

    broker.consume(handle, limit=limit)
    return outcome


def process_due_jobs(
    db: Session, handlers: dict[str, Handler], now: datetime | None = None, limit: int = 100, broker: Broker | None = None
) -> dict:
    """One worker pass: relay due jobs to the broker, then consume what is waiting."""
    from .broker import get_broker

    broker = broker or get_broker()
    now = now or utcnow()
    published = relay_due_jobs(db, broker, now, limit)
    result = consume_jobs(db, broker, handlers, now, limit)
    result["published"] = published
    return result


def retry_job(db: Session, job: QueueJob, now: datetime | None = None) -> QueueJob:
    """Put a dead (or waiting) job back in line right away (relayed on the next pass)."""
    job.status = PENDING
    job.run_at = now or utcnow()
    job.finished_at = None
    if job.attempts >= job.max_attempts:
        job.max_attempts = job.attempts + DEFAULT_MAX_ATTEMPTS
    return job


def stats(db: Session) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for status, total in db.query(QueueJob.status, func.count(QueueJob.id)).group_by(QueueJob.status):
        counts[status] = total
    return counts

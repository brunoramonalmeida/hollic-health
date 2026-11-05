"""Unit tests for the outbox queue and the reminder window (pure logic plus a
direct database session, no HTTP)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.mailer import MemorySender
from app.models import QueueJob
from app.queue import (
    BACKOFF_CAP,
    DEAD,
    DONE,
    PENDING,
    PROCESSING,
    STALE_PROCESSING,
    backoff,
    enqueue,
    process_due_jobs as _process_due_jobs,
    retry_job,
    stats,
)
from app.reminders import DAY_BEFORE, HOUR_BEFORE, MIN_LEAD, due_for_reminder

NOW = datetime(2026, 10, 22, 10, 0)


@pytest.fixture()
def db(client):
    session = client.session_factory()
    yield session
    session.close()


def process_due_jobs(db, handlers, now=None, **kwargs):
    """The classic two-number summary of a pass (the broker adds more keys)."""
    result = _process_due_jobs(db, handlers, now, **kwargs)
    return {"delivered": result["delivered"], "failed": result["failed"]}


def handlers(sender):
    return {"email": lambda payload: sender.send(payload["to"], payload["subject"], payload["body"])}


def email_job(db, to="a@example.com", **kwargs):
    kwargs.setdefault("run_at", NOW)
    job = enqueue(db, "email", {"to": to, "subject": "S", "body": "B"}, event="test", **kwargs)
    db.commit()
    return job


# --- back-off -----------------------------------------------------------------------


def test_backoff_doubles_and_is_capped():
    assert backoff(1) == timedelta(minutes=1)
    assert backoff(2) == timedelta(minutes=2)
    assert backoff(3) == timedelta(minutes=4)
    assert backoff(5) == timedelta(minutes=16)
    assert backoff(20) == BACKOFF_CAP


# --- processing ---------------------------------------------------------------------


def test_successful_job_is_done_and_delivered(db):
    sender = MemorySender()
    job = email_job(db)
    result = process_due_jobs(db, handlers(sender), NOW)
    assert result == {"delivered": 1, "failed": 0}
    db.refresh(job)
    assert job.status == DONE
    assert job.attempts == 1
    assert job.finished_at == NOW
    assert sender.sent == [{"to": "a@example.com", "subject": "S", "body": "B"}]


def test_failed_job_is_retried_later_with_backoff(db):
    sender = MemorySender(fail_times=1)
    job = email_job(db)
    assert process_due_jobs(db, handlers(sender), NOW) == {"delivered": 0, "failed": 1}
    db.refresh(job)
    assert job.status == PENDING
    assert job.attempts == 1
    assert job.run_at == NOW + timedelta(minutes=1)
    assert "SMTP unavailable" in job.last_error
    # Not due yet: nothing happens.
    assert process_due_jobs(db, handlers(sender), NOW + timedelta(seconds=30)) == {"delivered": 0, "failed": 0}
    # Due: delivered on the second try.
    assert process_due_jobs(db, handlers(sender), NOW + timedelta(minutes=1)) == {"delivered": 1, "failed": 0}
    db.refresh(job)
    assert job.status == DONE
    assert job.attempts == 2
    assert job.last_error == ""
    assert len(sender.sent) == 1


def test_job_dies_after_max_attempts(db):
    sender = MemorySender(fail_times=99)
    job = email_job(db, max_attempts=3)
    when = NOW
    for _ in range(3):
        process_due_jobs(db, handlers(sender), when)
        db.refresh(job)
        when = job.run_at
    assert job.status == DEAD
    assert job.attempts == 3
    assert job.finished_at is not None
    # Dead jobs are not picked up again.
    assert process_due_jobs(db, handlers(sender), when + timedelta(days=1)) == {"delivered": 0, "failed": 0}


def test_retry_dead_job_gives_it_new_attempts(db):
    sender = MemorySender(fail_times=1)
    job = email_job(db, max_attempts=1)
    process_due_jobs(db, handlers(sender), NOW)
    db.refresh(job)
    assert job.status == DEAD
    retry_job(db, job, NOW)
    db.commit()
    assert job.status == PENDING
    assert job.max_attempts > job.attempts
    assert process_due_jobs(db, handlers(sender), NOW) == {"delivered": 1, "failed": 0}


def test_unknown_kind_fails_and_is_recorded(db):
    job = enqueue(db, "sms", {"to": "x"}, max_attempts=1, run_at=NOW)
    db.commit()
    process_due_jobs(db, handlers(MemorySender()), NOW)
    db.refresh(job)
    assert job.status == DEAD
    assert "No handler" in job.last_error


def test_future_jobs_wait_for_their_run_at(db):
    sender = MemorySender()
    email_job(db, run_at=NOW + timedelta(hours=1))
    assert process_due_jobs(db, handlers(sender), NOW) == {"delivered": 0, "failed": 0}
    assert process_due_jobs(db, handlers(sender), NOW + timedelta(hours=1)) == {"delivered": 1, "failed": 0}


def test_stale_processing_job_is_recovered(db):
    """A worker that crashed mid-send leaves a job in 'processing'; it is retried."""
    sender = MemorySender()
    job = email_job(db)
    job.status = PROCESSING
    job.run_at = NOW - STALE_PROCESSING - timedelta(minutes=1)
    db.commit()
    assert process_due_jobs(db, handlers(sender), NOW) == {"delivered": 1, "failed": 0}
    fresh = email_job(db, to="b@example.com")
    fresh.status = PROCESSING
    fresh.run_at = NOW - timedelta(minutes=1)  # another worker is on it right now
    db.commit()
    assert process_due_jobs(db, handlers(sender), NOW) == {"delivered": 0, "failed": 0}


def test_jobs_run_oldest_first_and_respect_limit(db):
    sender = MemorySender()
    email_job(db, to="second@example.com", run_at=NOW - timedelta(minutes=1))
    email_job(db, to="first@example.com", run_at=NOW - timedelta(minutes=2))
    process_due_jobs(db, handlers(sender), NOW, limit=1)
    assert [item["to"] for item in sender.sent] == ["first@example.com"]


def test_stats_counts_every_status(db):
    sender = MemorySender(fail_times=1)
    email_job(db, max_attempts=1)  # runs first and fails -> dead
    email_job(db)  # delivered
    process_due_jobs(db, handlers(sender), NOW)
    counts = stats(db)
    assert counts["done"] == 1
    assert counts["dead"] == 1
    assert counts["pending"] == 0
    assert counts["processing"] == 0
    assert db.query(QueueJob).count() == 2


# --- reminder window ----------------------------------------------------------------


def appointment(marked_at, status="agendada"):
    return SimpleNamespace(marked_at=marked_at, status=status)


def test_day_reminder_window():
    slot = NOW + timedelta(hours=23)
    assert due_for_reminder(appointment(slot), NOW, DAY_BEFORE, None) is True
    assert due_for_reminder(appointment(NOW + DAY_BEFORE), NOW, DAY_BEFORE, None) is True  # boundary
    assert due_for_reminder(appointment(NOW + timedelta(hours=25)), NOW, DAY_BEFORE, None) is False
    assert due_for_reminder(appointment(slot), NOW, DAY_BEFORE, NOW - timedelta(hours=1)) is False  # already sent


def test_hour_reminder_window():
    assert due_for_reminder(appointment(NOW + timedelta(minutes=59)), NOW, HOUR_BEFORE, None) is True
    assert due_for_reminder(appointment(NOW + timedelta(minutes=61)), NOW, HOUR_BEFORE, None) is False


def test_no_reminder_for_cancelled_completed_or_imminent_slots():
    assert due_for_reminder(appointment(NOW + timedelta(hours=2), "cancelada"), NOW, DAY_BEFORE, None) is False
    assert due_for_reminder(appointment(NOW + timedelta(hours=2), "realizada"), NOW, DAY_BEFORE, None) is False
    # Worker was down: the slot is in two minutes, too late to be useful.
    assert due_for_reminder(appointment(NOW + MIN_LEAD - timedelta(minutes=1)), NOW, HOUR_BEFORE, None) is False
    assert due_for_reminder(appointment(NOW - timedelta(minutes=1)), NOW, HOUR_BEFORE, None) is False

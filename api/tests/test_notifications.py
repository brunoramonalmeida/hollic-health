"""API tests: events create in-app notifications and queued emails, reminders
fire once, the queue is administered by admins."""

from datetime import date, datetime, timedelta

import pytest

from app.mailer import MemorySender, set_sender
from app.reminders import schedule_reminders
from app.worker import tick
from conftest import activate_plan, auth, auth_as, ensure_plan, sign

MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)


@pytest.fixture()
def ctx(client, outbox):
    admin = auth(client)
    plan = ensure_plan(client, admin, discount_percent=20)
    professional = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    holder = client.post(
        "/customers", headers=admin, json={"name": "Foo Holder", "email": "holder@example.com"}
    ).json()["id"]
    client_headers = auth_as(client, "client", customer_id=holder)  # creates client<id>@example.com
    attendant = auth_as(client, "attendant")
    outbox.sent.clear()

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.admin = admin
    c.attendant = attendant
    c.client_headers = client_headers
    c.plan = plan
    c.professional = professional
    c.holder = holder
    c.outbox = outbox

    def book(headers=admin, marked_at=MONDAY_8, **overrides):
        payload = {"customer_id": holder, "professional_id": professional, "marked_at": marked_at.isoformat(), **overrides}
        response = client.post("/appointments", headers=headers, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    def notifications(headers):
        return client.get("/notifications", headers=headers).json()

    def run_worker():
        return client.post("/queue/run", headers=admin).json()

    def jobs(status=None):
        path = "/queue" + (f"?status={status}" if status else "")
        return client.get(path, headers=admin).json()

    c.book = book
    c.notifications = notifications
    c.run_worker = run_worker
    c.jobs = jobs
    return c


def titles(rows):
    return [row["title"] for row in rows]


# --- appointment events ---------------------------------------------------------------


def test_booking_notifies_the_customer_in_app_and_by_email(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.outbox.sent.clear()
    ctx.client.post("/notifications/read-all", headers=ctx.client_headers)
    item = ctx.book()
    mine = ctx.notifications(ctx.client_headers)
    assert mine[0]["event"] == "appointment.created"
    assert mine[0]["title"] == "Appointment scheduled for 26/10/2026 at 08:00"
    assert "Lorem Ipsum" in mine[0]["body"]
    assert "20% plan discount" in mine[0]["body"]
    assert mine[0]["link"] == f"/appointments/{item['id']}"
    assert mine[0]["read_at"] is None
    # Emails are queued, not sent yet.
    pending = ctx.jobs("pending")
    assert {job["to"] for job in pending if job["event"] == "appointment.created"} == {
        "holder@example.com",
        f"client{ctx.holder}@example.com",
    }
    assert ctx.outbox.sent == []
    result = ctx.run_worker()
    assert result["queue"]["delivered"] >= 2
    assert {mail["to"] for mail in ctx.outbox.sent} >= {"holder@example.com", f"client{ctx.holder}@example.com"}
    booking_mails = [mail for mail in ctx.outbox.sent if mail["subject"] == "Appointment scheduled for 26/10/2026 at 08:00"]
    assert {mail["to"] for mail in booking_mails} == {"holder@example.com", f"client{ctx.holder}@example.com"}
    assert "Amount: R$ 120,00 (20% plan discount)" in booking_mails[0]["body"]


def test_staff_booking_does_not_notify_staff_but_client_booking_does(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    before = len(ctx.notifications(ctx.attendant))
    ctx.book(headers=ctx.attendant)
    assert len(ctx.notifications(ctx.attendant)) == before
    ctx.book(headers=ctx.client_headers, marked_at=MONDAY_9)
    staff_rows = ctx.notifications(ctx.attendant)
    assert staff_rows[0]["title"] == "Foo Holder booked an appointment for 26/10/2026 at 09:00"
    assert ctx.notifications(ctx.admin)[0]["title"] == staff_rows[0]["title"]


def test_reschedule_cancel_and_complete_events(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    item = ctx.book()
    base = {"customer_id": ctx.holder, "professional_id": ctx.professional}
    ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "marked_at": MONDAY_9.isoformat()})
    ctx.client.put(f"/appointments/{item['id']}", headers=ctx.admin, json={**base, "marked_at": MONDAY_9.isoformat(), "notes": "x"})
    sign(ctx.client, item["id"])  # completion = the professional's signature
    other = ctx.book(marked_at=MONDAY_8)
    ctx.client.put(f"/appointments/{other['id']}", headers=ctx.client_headers, json={**base, "marked_at": MONDAY_8.isoformat(), "status": "cancelada"})
    mine = titles(ctx.notifications(ctx.client_headers))
    assert mine[:4] == [
        "Appointment of 26/10/2026 at 08:00 cancelled",
        "Appointment scheduled for 26/10/2026 at 08:00",
        "Appointment of 26/10/2026 at 09:00 completed",
        "Appointment moved to 26/10/2026 at 09:00",
    ]
    # Editing only the notes produced nothing; the client's cancellation reached the staff.
    assert mine.count("Appointment moved to 26/10/2026 at 09:00") == 1
    assert ctx.notifications(ctx.attendant)[0]["title"] == "Foo Holder cancelled the appointment of 26/10/2026 at 08:00"


# --- subscription events ----------------------------------------------------------------


def test_subscription_payment_and_cancellation_events(ctx):
    ctx.client.post("/notifications/read-all", headers=ctx.client_headers)
    sub = ctx.client.post(
        "/subscriptions",
        headers=ctx.attendant,
        json={"customer_id": ctx.holder, "modality_id": ctx.plan["id"], "joined_on": "2026-10-01", "expires_on": "2027-09-30"},
    ).json()
    first = sub["installments"][0]
    ctx.client.put(f"/subscriptions/{sub['id']}/installments/{first['id']}", headers=ctx.attendant, json={"paid_on": "2026-10-01"})
    ctx.client.post(f"/subscriptions/{sub['id']}/cancel", headers=ctx.client_headers)
    mine = ctx.notifications(ctx.client_headers)
    assert titles(mine)[:3] == ["Plan Foo cancelled", "Payment received: installment 1 of Plan Foo", "Welcome to Plan Foo"]
    assert "R$ 99,00" in mine[1]["body"]
    assert "ativa" in mine[1]["body"]
    assert "first installment is paid" in mine[2]["body"]
    assert ctx.notifications(ctx.attendant)[0]["title"] == "Foo Holder cancelled the plan Plan Foo"
    events = {job["event"] for job in ctx.jobs()}
    assert {"subscription.created", "installment.paid", "subscription.cancelled"} <= events


def test_new_user_receives_a_welcome(ctx):
    created = ctx.client.post(
        "/users",
        headers=ctx.admin,
        json={"name": "New Att", "email": "newatt@example.com", "role": "attendant", "password": "secret1"},
    )
    assert created.status_code == 201
    headers = auth(ctx.client, "newatt@example.com", "secret1")
    mine = ctx.notifications(headers)
    assert mine[0]["event"] == "user.created"
    assert [job for job in ctx.jobs("pending") if job["to"] == "newatt@example.com"]


# --- notification endpoints ---------------------------------------------------------------


def test_summary_and_read_state(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.client.post("/notifications/read-all", headers=ctx.client_headers)
    ctx.book()
    ctx.book(marked_at=MONDAY_9)
    summary = ctx.client.get("/notifications/summary", headers=ctx.client_headers).json()
    assert summary["unread"] == 2
    assert len(summary["latest"]) >= 2
    first = summary["latest"][0]
    read = ctx.client.post(f"/notifications/{first['id']}/read", headers=ctx.client_headers)
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert ctx.client.get("/notifications/summary", headers=ctx.client_headers).json()["unread"] == 1
    assert len(ctx.client.get("/notifications?unread_only=true", headers=ctx.client_headers).json()) == 1
    ctx.client.post(f"/notifications/{first['id']}/unread", headers=ctx.client_headers)
    assert ctx.client.get("/notifications/summary", headers=ctx.client_headers).json()["unread"] == 2
    ctx.client.post("/notifications/read-all", headers=ctx.client_headers)
    assert ctx.client.get("/notifications/summary", headers=ctx.client_headers).json()["unread"] == 0


def test_notifications_are_private(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book()
    mine = ctx.notifications(ctx.client_headers)[0]
    assert ctx.client.post(f"/notifications/{mine['id']}/read", headers=ctx.attendant).status_code == 404
    assert all(row["id"] != mine["id"] for row in ctx.notifications(ctx.attendant))


# --- reminders ---------------------------------------------------------------------------


def test_reminders_fire_once_a_day_before_and_an_hour_before(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    item = ctx.book()
    ctx.client.post("/notifications/read-all", headers=ctx.client_headers)
    db = ctx.client.session_factory()
    try:
        # 30 hours before: nothing yet.
        assert schedule_reminders(db, MONDAY_8 - timedelta(hours=30)) == {"day": 0, "hour": 0}
        # 23 hours before: day reminder, exactly once.
        assert schedule_reminders(db, MONDAY_8 - timedelta(hours=23)) == {"day": 1, "hour": 0}
        assert schedule_reminders(db, MONDAY_8 - timedelta(hours=22)) == {"day": 0, "hour": 0}
        # 50 minutes before: hour reminder, exactly once.
        assert schedule_reminders(db, MONDAY_8 - timedelta(minutes=50)) == {"day": 0, "hour": 1}
        assert schedule_reminders(db, MONDAY_8 - timedelta(minutes=40)) == {"day": 0, "hour": 0}
    finally:
        db.close()
    mine = ctx.notifications(ctx.client_headers)
    assert titles(mine)[:2] == ["Reminder: appointment in 1 hour (08:00)", "Reminder: appointment tomorrow at 08:00"]
    assert mine[0]["link"] == f"/appointments/{item['id']}"
    assert {job["event"] for job in ctx.jobs("pending")} >= {"appointment.reminder_day", "appointment.reminder_hour"}


def test_no_reminders_for_cancelled_appointments(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    item = ctx.book()
    ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": MONDAY_8.isoformat(), "status": "cancelada"},
    )
    db = ctx.client.session_factory()
    try:
        assert schedule_reminders(db, MONDAY_8 - timedelta(hours=23)) == {"day": 0, "hour": 0}
    finally:
        db.close()


def test_moving_an_appointment_reminds_the_new_slot(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    item = ctx.book()
    db = ctx.client.session_factory()
    try:
        assert schedule_reminders(db, MONDAY_8 - timedelta(hours=23))["day"] == 1
    finally:
        db.close()
    ctx.client.put(
        f"/appointments/{item['id']}",
        headers=ctx.admin,
        json={"customer_id": ctx.holder, "professional_id": ctx.professional, "marked_at": MONDAY_9.isoformat()},
    )
    db = ctx.client.session_factory()
    try:
        assert schedule_reminders(db, MONDAY_9 - timedelta(hours=23))["day"] == 1
    finally:
        db.close()


def test_worker_tick_delivers_reminder_emails(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book()
    db = ctx.client.session_factory()
    try:
        tick(db, MONDAY_8 - timedelta(hours=30))  # flush the booking emails, outside the reminder window
        ctx.outbox.sent.clear()
        result = tick(db, MONDAY_8 - timedelta(hours=23))
    finally:
        db.close()
    assert result["reminders"] == {"day": 1, "hour": 0}
    assert result["queue"]["delivered"] >= 1
    assert any(mail["subject"] == "Reminder: appointment tomorrow at 08:00" for mail in ctx.outbox.sent)


# --- queue administration ------------------------------------------------------------------


def test_failed_emails_are_retried_and_can_be_requeued_when_dead(ctx):
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.run_worker()
    flaky = MemorySender(fail_times=1000)
    set_sender(flaky)
    ctx.book()
    first = ctx.run_worker()
    assert first["queue"]["failed"] >= 1
    pending = [job for job in ctx.jobs("pending") if job["event"] == "appointment.created"]
    assert pending and all(job["attempts"] == 1 and "SMTP unavailable" in job["last_error"] for job in pending)
    assert datetime.fromisoformat(pending[0]["run_at"]) > datetime.fromisoformat(pending[0]["created_at"])
    # Force a dead letter by exhausting the attempts through the queue API.
    db = ctx.client.session_factory()
    try:
        from app.models import QueueJob

        for job in db.query(QueueJob).filter(QueueJob.status == "pending").all():
            job.attempts = job.max_attempts - 1
            job.run_at = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()
    ctx.run_worker()
    dead = ctx.jobs("dead")
    assert dead
    stats = ctx.client.get("/queue/stats", headers=ctx.admin).json()
    assert stats["dead"] == len(dead)
    # Mail is back: retry one job, then all the others.
    set_sender(MemorySender())
    retried = ctx.client.post(f"/queue/{dead[0]['id']}/retry", headers=ctx.admin)
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert ctx.client.post("/queue/retry-dead", headers=ctx.admin).json()["dead"] == 0
    result = ctx.run_worker()
    assert result["queue"]["delivered"] == len(dead)
    assert ctx.client.get("/queue/stats", headers=ctx.admin).json()["pending"] == 0


def test_queue_validation(ctx):
    assert ctx.client.get("/queue?status=bogus", headers=ctx.admin).status_code == 400
    assert ctx.client.post("/queue/9999/retry", headers=ctx.admin).status_code == 404
    activate_plan(ctx.client, ctx.admin, ctx.holder, plan=ctx.plan)
    ctx.book()
    ctx.run_worker()
    done = ctx.jobs("done")[0]
    assert ctx.client.post(f"/queue/{done['id']}/retry", headers=ctx.admin).status_code == 409


@pytest.mark.parametrize("role", ["supervisor", "attendant", "client"])
def test_queue_is_admin_only(ctx, role):
    headers = ctx.client_headers if role == "client" else auth_as(ctx.client, role)
    assert ctx.client.get("/queue", headers=headers).status_code == 403
    assert ctx.client.post("/queue/run", headers=headers).status_code == 403

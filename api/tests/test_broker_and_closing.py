"""The message broker behind the outbox and the automatic monthly closing.

The RabbitMQ adapter is exercised against a fake of pika's channel API (the
real broker is not available in the test environment): durable topology,
persistent messages, publisher confirms, manual acks and dead-lettering.
"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock
from app import broker as broker_module
from app.broker import DEAD_EXCHANGE, DEAD_QUEUE, MemoryBroker, PublishError, RabbitMQBroker, make_broker
from app.closing import (
    KIND_CLOSE,
    KIND_SETTLE,
    closing_due,
    installments_for,
    period_bounds,
    previous_period,
    schedule_closing,
    schedule_monthly_closing,
)
from app.models import PayoutClosing, PayoutSettlement, QueueJob
from app.queue import DEAD, DONE, PENDING, QUEUED, consume_jobs, enqueue, process_due_jobs, relay_due_jobs
from app.worker import tick

# --- a faithful fake of the pika surface the adapter uses ----------------------------------


class FakeChannel:
    def __init__(self, *, nack_publish=False):
        self.is_closed = False
        self.exchanges = {}
        self.queues = {}
        self.bindings = []
        self.confirms = False
        self.qos = None
        self.messages = {}  # queue -> list of (properties, body)
        self.acked = []
        self.nacked = []
        self.nack_publish = nack_publish
        self._tag = 0
        self._unacked = {}

    def exchange_declare(self, exchange, exchange_type, durable):
        self.exchanges[exchange] = {"type": exchange_type, "durable": durable}

    def queue_declare(self, queue, durable, arguments=None, passive=False):
        if not passive:
            self.queues[queue] = {"durable": durable, "arguments": arguments or {}}
            self.messages.setdefault(queue, [])
        return SimpleNamespace(method=SimpleNamespace(message_count=len(self.messages.get(queue, []))))

    def queue_bind(self, queue, exchange, routing_key):
        self.bindings.append((queue, exchange, routing_key))

    def confirm_delivery(self):
        self.confirms = True

    def basic_qos(self, prefetch_count):
        self.qos = prefetch_count

    def basic_publish(self, exchange, routing_key, body, properties, mandatory):
        assert self.confirms, "publisher confirms must be on before publishing"
        if self.nack_publish:
            raise RuntimeError("broker nacked the message")
        for queue, bound_exchange, key in self.bindings:
            if bound_exchange == exchange and key == routing_key:
                self.messages[queue].append((properties, body))
                return
        raise RuntimeError("unroutable")  # what pika raises with mandatory=True

    def basic_get(self, queue, auto_ack):
        assert auto_ack is False
        if not self.messages[queue]:
            return None, None, None
        properties, body = self.messages[queue].pop(0)
        self._tag += 1
        self._unacked[self._tag] = (queue, properties, body)
        return SimpleNamespace(delivery_tag=self._tag), properties, body

    def basic_ack(self, delivery_tag):
        self.acked.append(delivery_tag)
        self._unacked.pop(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        queue, properties, body = self._unacked.pop(delivery_tag)
        self.nacked.append((delivery_tag, requeue))
        if requeue:
            self.messages[queue].insert(0, (properties, body))
        else:
            dlx = self.queues[queue]["arguments"].get("x-dead-letter-exchange")
            key = self.queues[queue]["arguments"].get("x-dead-letter-routing-key")
            for bound_queue, exchange, routing_key in self.bindings:
                if exchange == dlx and routing_key == key:
                    self.messages[bound_queue].append((properties, body))


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel
        self.is_closed = False

    def channel(self):
        return self._channel

    def close(self):
        self.is_closed = True


@pytest.fixture()
def fake_pika(monkeypatch):
    """``import pika`` inside the adapter resolves to this stub."""
    import sys

    class BasicProperties:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    stub = SimpleNamespace(BasicProperties=BasicProperties, URLParameters=lambda url: url, BlockingConnection=lambda params: None)
    monkeypatch.setitem(sys.modules, "pika", stub)
    return stub


def test_rabbitmq_topology_is_durable_with_dead_lettering(fake_pika):
    channel = FakeChannel()
    broker = RabbitMQBroker("amqp://guest:guest@localhost/%2F", connection=FakeConnection(channel))
    broker.channel()
    assert channel.exchanges["sante"] == {"type": "direct", "durable": True}
    assert channel.exchanges[DEAD_EXCHANGE]["durable"] is True
    assert channel.queues["sante.jobs"]["durable"] is True
    assert channel.queues["sante.jobs"]["arguments"] == {"x-dead-letter-exchange": DEAD_EXCHANGE, "x-dead-letter-routing-key": DEAD_QUEUE}
    assert channel.queues[DEAD_QUEUE]["durable"] is True
    assert ("sante.jobs", "sante", "sante.jobs") in channel.bindings
    assert (DEAD_QUEUE, DEAD_EXCHANGE, DEAD_QUEUE) in channel.bindings
    assert channel.confirms is True and channel.qos == broker.prefetch


def test_rabbitmq_publish_is_persistent_and_confirmed(fake_pika):
    channel = FakeChannel()
    broker = RabbitMQBroker("amqp://x", connection=FakeConnection(channel))
    broker.publish({"job_id": 7}, message_id="job-7-1", headers={"kind": "email"})
    properties, body = channel.messages["sante.jobs"][0]
    assert properties.delivery_mode == 2  # persistent
    assert properties.message_id == "job-7-1" and properties.headers == {"kind": "email"}
    assert json.loads(body) == {"job_id": 7}
    assert broker.depth() == 1
    # A nack from the broker surfaces as PublishError: the outbox row stays pending.
    nacking = RabbitMQBroker("amqp://x", connection=FakeConnection(FakeChannel(nack_publish=True)))
    with pytest.raises(PublishError):
        nacking.publish({"job_id": 8}, message_id="job-8-1")


def test_rabbitmq_consume_acks_nacks_and_dead_letters(fake_pika):
    channel = FakeChannel()
    broker = RabbitMQBroker("amqp://x", connection=FakeConnection(channel))
    broker.publish({"job_id": 1}, message_id="a")
    broker.publish({"job_id": 2}, message_id="b")  # handler says "dead-letter"
    broker.channel()
    channel.messages["sante.jobs"].append((None, b"not json"))  # garbage on the queue
    seen = []

    def handler(message):
        seen.append(message["job_id"])
        return message["job_id"] != 2

    assert broker.consume(handler) == 3
    assert seen == [1, 2]
    assert channel.acked == [1]
    assert [requeue for _, requeue in channel.nacked] == [False, False]
    assert len(channel.messages[DEAD_QUEUE]) == 2  # the rejected message and the garbage
    assert broker.depth() == 0
    # A crashing handler leaves the message for redelivery (nack with requeue) and stops the batch.
    broker.publish({"job_id": 3}, message_id="c")

    def crash(message):
        raise RuntimeError("boom")

    assert broker.consume(crash) == 0
    assert channel.nacked[-1][1] is True
    assert broker.depth() == 1
    broker.close()
    assert broker._connection is None


def test_make_broker_from_url():
    assert isinstance(make_broker("memory"), MemoryBroker)
    assert isinstance(make_broker("amqp://guest:guest@rabbit:5672/%2F"), RabbitMQBroker)
    with pytest.raises(ValueError):
        make_broker("redis://x")


def test_memory_broker_semantics():
    broker = MemoryBroker()
    broker.publish({"job_id": 1}, message_id="a")
    broker.publish({"job_id": 2}, message_id="b")
    assert broker.depth() == 2
    assert broker.consume(lambda m: m["job_id"] == 1) == 2
    assert [m.body for m in broker.dead] == [{"job_id": 2}]
    broker.publish({"job_id": 3}, message_id="c")

    def crash(message):
        raise RuntimeError("boom")

    assert broker.consume(crash) == 0 and broker.depth() == 1  # kept for the next pass


# --- outbox relay + consume -------------------------------------------------------------


NOW = datetime(2026, 11, 1, 8, 0)


@pytest.fixture()
def db(client):
    session = client.session_factory()
    yield session
    session.close()


def test_relay_marks_rows_queued_only_after_the_broker_confirmed(db):
    broker = MemoryBroker()
    enqueue(db, "email", {"to": "a@example.com", "subject": "s", "body": "b"}, run_at=NOW)
    later = enqueue(db, "email", {"to": "b@example.com", "subject": "s", "body": "b"}, run_at=NOW + timedelta(hours=1))
    db.commit()
    assert relay_due_jobs(db, broker, NOW) == 1
    rows = db.query(QueueJob).order_by(QueueJob.id).all()
    assert rows[0].status == QUEUED and rows[1].status == PENDING
    assert broker.published[0].body["job_id"] == rows[0].id and broker.published[0].message_id == f"job-{rows[0].id}-1"
    assert broker.published[0].headers["kind"] == "email"

    class Down(MemoryBroker):
        def publish(self, body, *, message_id, headers=None):
            raise PublishError("connection refused")

    assert relay_due_jobs(db, Down(), NOW + timedelta(hours=2)) == 0
    db.refresh(later)
    assert later.status == PENDING  # nothing confirmed, nothing marked


def test_consume_runs_jobs_and_skips_duplicates_and_garbage(db):
    broker = MemoryBroker()
    sent = []
    job = enqueue(db, "email", {"to": "a@example.com"}, run_at=NOW)
    db.commit()
    relay_due_jobs(db, broker, NOW)
    handlers = {"email": lambda payload: sent.append(payload["to"])}
    broker.publish({"job_id": job.id}, message_id="dup")  # duplicate delivery
    broker.publish({"job_id": 99999}, message_id="missing")  # points nowhere
    broker.publish({"nonsense": True}, message_id="garbage")
    result = consume_jobs(db, broker, handlers, NOW)
    assert result == {"delivered": 1, "failed": 0, "skipped": 1, "dead_lettered": 2}
    assert sent == ["a@example.com"]
    assert db.get(QueueJob, job.id).status == DONE
    assert len(broker.dead) == 2


def test_failed_job_goes_back_to_pending_and_is_relayed_when_due(db):
    broker = MemoryBroker()
    attempts = []

    def flaky(payload):
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("smtp down")

    job = enqueue(db, "email", {"to": "a@example.com"}, run_at=NOW)
    db.commit()
    result = process_due_jobs(db, {"email": flaky}, NOW, broker=broker)
    assert result["published"] == 1 and result["failed"] == 1
    db.refresh(job)
    assert job.status == PENDING and job.run_at == NOW + timedelta(minutes=1) and job.attempts == 1
    assert process_due_jobs(db, {"email": flaky}, NOW + timedelta(seconds=30), broker=broker)["published"] == 0  # not due yet
    result = process_due_jobs(db, {"email": flaky}, NOW + timedelta(minutes=1), broker=broker)
    assert result["published"] == 1 and result["delivered"] == 1
    db.refresh(job)
    assert job.status == DONE and broker.depth() == 0


def test_stale_queued_job_is_published_again(db):
    broker = MemoryBroker()
    job = enqueue(db, "email", {"to": "a@example.com"}, run_at=NOW)
    db.commit()
    relay_due_jobs(db, broker, NOW)
    broker.ready.clear()  # the message vanished from the broker
    assert relay_due_jobs(db, broker, NOW + timedelta(minutes=10)) == 0  # too early to suspect
    assert relay_due_jobs(db, broker, NOW + timedelta(minutes=31)) == 1
    db.refresh(job)
    assert job.status == QUEUED and broker.depth() == 1


def test_queue_stats_report_the_broker(client):
    body = client.get("/queue/stats", headers=auth(client)).json()
    assert body["broker"] == "memory" and body["broker_depth"] == 0 and "queued" in body


# --- monthly closing: rules ---------------------------------------------------------------


def test_closing_calendar():
    assert previous_period(date(2026, 11, 1)) == "2026-10"
    assert previous_period(date(2026, 1, 15)) == "2025-12"
    assert period_bounds("2026-10") == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert closing_due(date(2026, 11, 1), closing_day=1) == "2026-10"
    assert closing_due(date(2026, 11, 4), closing_day=5) is None
    assert closing_due(date(2026, 11, 5), closing_day=5) == "2026-10"


def test_installments_grow_with_the_amount():
    assert installments_for(150000, step_cents=200000, maximum=3) == 1
    assert installments_for(200000, step_cents=200000, maximum=3) == 2
    assert installments_for(450000, step_cents=200000, maximum=3) == 3
    assert installments_for(5000000, step_cents=200000, maximum=3) == 3  # capped
    assert installments_for(0) == 1


# --- monthly closing: end to end through the worker ----------------------------------------


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin)
    lorem = client.post("/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}).json()["id"]
    dolor = client.post("/professionals", headers=admin, json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 7000}).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)

    def book(professional, when):
        response = client.post("/appointments", headers=admin, json={"customer_id": holder, "professional_id": professional, "marked_at": when.isoformat()})
        assert response.status_code == 201, response.text
        sign(client, response.json()["id"])
        return response.json()

    # Lorem: two signed appointments in October; Dolor: none (nothing to settle).
    book(lorem, datetime(2026, 10, 19, 8, 0))
    book(lorem, datetime(2026, 10, 20, 8, 0))
    return SimpleNamespace(client=client, admin=admin, lorem=lorem, dolor=dolor, lorem_login=auth_as(client, "professional", professional_id=lorem))


def run_worker_until_idle(db, now, passes=5):
    results = []
    for _ in range(passes):
        results.append(tick(db, now))
    return results


def test_monthly_closing_settles_every_payee_through_the_queue(ctx):
    db = ctx.client.session_factory()
    try:
        # On the 1st the previous month is scheduled, once.
        first = tick(db, datetime(2026, 11, 1, 0, 5))
        assert first["closing"] == {"scheduled": 1}
        closing = db.query(PayoutClosing).filter(PayoutClosing.period == "2026-10").one()
        assert closing.period_end == date(2026, 10, 31)
        # The close job ran in that pass (relayed + consumed) and fanned out one settle job per payee.
        db.refresh(closing)
        assert closing.status == "dispatched"
        payees = 2 + 1  # Lorem, Dolor + Clinic Foo
        assert closing.payees_total == payees
        settle_jobs = db.query(QueueJob).filter(QueueJob.kind == KIND_SETTLE).all()
        assert len(settle_jobs) == payees and all(j.status == PENDING for j in settle_jobs)
        # Next pass: the settle jobs are relayed and consumed.
        second = tick(db, datetime(2026, 11, 1, 0, 6))
        assert second["closing"] == {"scheduled": 0}  # never twice
        assert second["queue"]["delivered"] >= payees
        db.refresh(closing)
        assert closing.status == "done"
        assert (closing.settlements_created, closing.payees_skipped) == (2, 1)  # Lorem + the clinic; Dolor had nothing
        settlements = db.query(PayoutSettlement).filter(PayoutSettlement.closing_id == closing.id).all()
        by_type = {s.payee_type: s for s in settlements}
        lorem = by_type["professional"]
        assert lorem.professional_id == ctx.lorem and lorem.earned_cents == 10000 and lorem.period_end == date(2026, 10, 31)
        assert lorem.installments_count == 1  # 100,00 is under the step
        assert lorem.payments[0].due_on == date(2026, 11, 5)  # period end + 5 days
        assert lorem.notes == "Automatic monthly closing 2026-10" and lorem.created_by_id is None
        assert by_type["clinic"].earned_cents == 4000
        assert all(j.status == DONE for j in db.query(QueueJob).filter(QueueJob.kind.in_([KIND_CLOSE, KIND_SETTLE])).all())
    finally:
        db.close()
    # The professional was told; the API shows the settlement as automatic.
    titles = [n["title"] for n in ctx.client.get("/notifications", headers=ctx.lorem_login).json()]
    assert titles[0].startswith("Settlement 19/10/2026 – 31/10/2026: R$ 100,00 to receive")
    mine = ctx.client.get("/payouts/settlements", headers=ctx.lorem_login).json()
    assert mine[0]["closing_id"] is not None
    closings = ctx.client.get("/payouts/closings", headers=ctx.admin).json()
    assert closings[0]["period"] == "2026-10" and closings[0]["automatic"] is True and closings[0]["status"] == "done"


def test_settle_job_is_idempotent_and_a_failure_does_not_block_other_payees(ctx):
    db = ctx.client.session_factory()
    try:
        closing = schedule_closing(db, "2026-10")
        db.commit()
        tick(db, datetime(2026, 11, 2, 0, 0))  # close -> fan out
        # Re-delivering a settle job must not create a second settlement.
        from app.closing import handle_settle
        from app.payout_service import create_settlement

        payload = {"closing_id": closing.id, "payee_type": "professional", "payee_id": ctx.lorem}
        handle_settle(db, payload, create_settlement)
        handle_settle(db, payload, create_settlement)
        assert db.query(PayoutSettlement).filter(PayoutSettlement.professional_id == ctx.lorem).count() == 1
        # A payee whose job blows up is retried on its own; the others are done.
        job = db.query(QueueJob).filter(QueueJob.kind == KIND_SETTLE, QueueJob.payload.contains('"payee_type": "clinic"')).one()
        job.payload = json.dumps({"closing_id": 9999, "payee_type": "clinic", "payee_id": 1})  # simulate a broken job
        db.commit()
        tick(db, datetime(2026, 11, 2, 0, 1))
        db.refresh(job)
        assert job.status == PENDING and job.attempts == 1 and "Closing 9999 not found" in job.last_error
        others = db.query(QueueJob).filter(QueueJob.kind == KIND_SETTLE, QueueJob.id != job.id).all()
        assert all(j.status == DONE for j in others)
    finally:
        db.close()


def test_admin_triggers_a_closing_by_hand_once(ctx):
    refused = ctx.client.post("/payouts/closings", headers=ctx.admin, json={"period": "2026/10"})
    assert refused.status_code == 422
    current = clock.today().strftime("%Y-%m")
    assert ctx.client.post("/payouts/closings", headers=ctx.admin, json={"period": current}).status_code == 400
    created = ctx.client.post("/payouts/closings", headers=ctx.admin, json={"period": "2026-09"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["period"] == "2026-09" and body["status"] == "scheduled" and body["automatic"] is False and body["triggered_by_name"] == "Foo Bar"
    assert ctx.client.post("/payouts/closings", headers=ctx.admin, json={"period": "2026-09"}).status_code == 409
    assert ctx.client.post("/payouts/closings", headers=ctx.lorem_login, json={"period": "2026-08"}).status_code == 403
    assert ctx.client.get("/payouts/closings", headers=ctx.lorem_login).status_code == 403
    # The worker (admin "run now") carries it through: nothing was signed in September, every payee is skipped.
    ctx.client.post("/queue/run", headers=ctx.admin)
    ctx.client.post("/queue/run", headers=ctx.admin)
    done = next(c for c in ctx.client.get("/payouts/closings", headers=ctx.admin).json() if c["period"] == "2026-09")
    assert done["status"] == "done" and done["settlements_created"] == 0 and done["payees_skipped"] == done["payees_total"] == 3

"""Message broker behind the outbox: RabbitMQ, or an in-memory stand-in.

Delivery guarantees, layer by layer:

1. **Transactional outbox.** Every job is a ``queue_jobs`` row written in the
   same database transaction as the business event (``queue.enqueue``), so a
   job is never lost between "the event happened" and "the message exists".
2. **Relay with publisher confirms.** The worker publishes each due job to
   the broker as a *persistent* message on a *durable* queue and only marks
   the row ``queued`` after the broker confirmed it (``confirm_delivery``).
   A crash before the confirm leaves the row ``pending``: it is published
   again (at-least-once).
3. **Consumers acknowledge manually** after the job ran and its outcome was
   committed. A consumer that dies mid-job never acks, so RabbitMQ
   redelivers the message to another consumer. Handlers are idempotent and
   the job row is the source of truth: a duplicate delivery of a ``done`` job
   is acked and skipped.
4. **Retries with back-off** are driven by the job row (``queue.run_job``):
   a failed handler puts the job back to ``pending`` with a later ``run_at``;
   the relay republishes it when due. After ``max_attempts`` the job is
   ``dead`` and administrators can retry it from the queue page.
5. **Dead-letter queue.** Messages that cannot even be parsed (no job id, job
   missing) are rejected without requeue and land in ``sante.jobs.dead``
   through the dead-letter exchange, for inspection.

``BROKER_URL=amqp://user:pass@host:5672/%2F`` selects RabbitMQ (``pika`` is
imported lazily so it is only needed then); ``BROKER_URL=memory`` (default,
tests) keeps everything in process with the same contract.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("sante.broker")

BROKER_URL = os.getenv("BROKER_URL", "memory")
EXCHANGE = os.getenv("BROKER_EXCHANGE", "sante")
QUEUE = os.getenv("BROKER_QUEUE", "sante.jobs")
DEAD_QUEUE = f"{QUEUE}.dead"
DEAD_EXCHANGE = f"{EXCHANGE}.dlx"
PREFETCH = int(os.getenv("BROKER_PREFETCH", "10"))

Handler = Callable[[dict], bool]  # returns True to ack, False to dead-letter


class PublishError(Exception):
    """The broker did not confirm the message: the outbox row stays pending."""


@dataclass
class Message:
    body: dict
    message_id: str
    headers: dict = field(default_factory=dict)


class Broker:
    """Contract shared by the RabbitMQ adapter and the in-memory broker."""

    def publish(self, body: dict, *, message_id: str, headers: dict | None = None) -> None:
        raise NotImplementedError

    def consume(self, handler: Handler, *, limit: int = 100) -> int:
        """Deliver up to ``limit`` waiting messages to ``handler``; returns how many were handled."""
        raise NotImplementedError

    def depth(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MemoryBroker(Broker):
    """Same semantics in process: FIFO, ack/nack, dead letters kept aside."""

    def __init__(self):
        self.ready: list[Message] = []
        self.dead: list[Message] = []
        self.published: list[Message] = []

    def publish(self, body: dict, *, message_id: str, headers: dict | None = None) -> None:
        message = Message(body=dict(body), message_id=message_id, headers=dict(headers or {}))
        self.ready.append(message)
        self.published.append(message)

    def consume(self, handler: Handler, *, limit: int = 100) -> int:
        handled = 0
        while self.ready and handled < limit:
            message = self.ready.pop(0)
            try:
                ok = handler(message.body)
            except Exception:  # noqa: BLE001 - a crashing handler must not lose the message
                log.exception("handler crashed; message %s requeued", message.message_id)
                self.ready.append(message)
                break
            if not ok:
                self.dead.append(message)
            handled += 1
        return handled

    def depth(self) -> int:
        return len(self.ready)


class RabbitMQBroker(Broker):
    """RabbitMQ through ``pika``: durable topology, persistent messages,
    publisher confirms, manual acks, dead-letter exchange."""

    def __init__(self, url: str, *, exchange: str = EXCHANGE, queue: str = QUEUE, prefetch: int = PREFETCH, connection=None):
        self.url = url
        self.exchange = exchange
        self.queue = queue
        self.prefetch = prefetch
        self._connection = connection  # injectable for tests
        self._channel = None

    # -- connection -----------------------------------------------------------------

    def _connect(self):
        if self._connection is None:
            import pika  # imported lazily: only RabbitMQ deployments need it

            self._connection = pika.BlockingConnection(pika.URLParameters(self.url))
        return self._connection

    def channel(self):
        if self._channel is None or getattr(self._channel, "is_closed", False):
            self._channel = self._connect().channel()
            self._declare(self._channel)
            self._channel.confirm_delivery()
            self._channel.basic_qos(prefetch_count=self.prefetch)
        return self._channel

    def _declare(self, channel) -> None:
        channel.exchange_declare(exchange=self.exchange, exchange_type="direct", durable=True)
        channel.exchange_declare(exchange=DEAD_EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(
            queue=self.queue,
            durable=True,
            arguments={"x-dead-letter-exchange": DEAD_EXCHANGE, "x-dead-letter-routing-key": DEAD_QUEUE},
        )
        channel.queue_bind(queue=self.queue, exchange=self.exchange, routing_key=self.queue)
        channel.queue_declare(queue=DEAD_QUEUE, durable=True)
        channel.queue_bind(queue=DEAD_QUEUE, exchange=DEAD_EXCHANGE, routing_key=DEAD_QUEUE)

    # -- publish / consume ----------------------------------------------------------

    def publish(self, body: dict, *, message_id: str, headers: dict | None = None) -> None:
        import pika

        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,  # persistent: survives a broker restart
            message_id=message_id,
            headers=headers or {},
        )
        try:
            # With confirms on, basic_publish raises when the broker nacks or the
            # message is unroutable (mandatory=True); either way the outbox row stays pending.
            self.channel().basic_publish(
                exchange=self.exchange,
                routing_key=self.queue,
                body=json.dumps(body).encode("utf-8"),
                properties=properties,
                mandatory=True,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a publish failure
            self._channel = None
            raise PublishError(str(exc)) from exc

    def consume(self, handler: Handler, *, limit: int = 100) -> int:
        channel = self.channel()
        handled = 0
        while handled < limit:
            method, properties, payload = channel.basic_get(queue=self.queue, auto_ack=False)
            if method is None:
                break
            try:
                body = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)  # -> dead-letter queue
                handled += 1
                continue
            try:
                ok = handler(body)
            except Exception:  # noqa: BLE001 - leave the message to be redelivered
                log.exception("handler crashed; message %s left unacked", getattr(properties, "message_id", "?"))
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                break
            if ok:
                channel.basic_ack(delivery_tag=method.delivery_tag)
            else:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            handled += 1
        return handled

    def depth(self) -> int:
        result = self.channel().queue_declare(queue=self.queue, durable=True, passive=True)
        return result.method.message_count

    def close(self) -> None:
        try:
            if self._connection is not None and not getattr(self._connection, "is_closed", False):
                self._connection.close()
        finally:
            self._connection = None
            self._channel = None


_broker: Broker | None = None


def make_broker(url: str | None = None) -> Broker:
    url = url or BROKER_URL
    if url == "memory":
        return MemoryBroker()
    if url.startswith(("amqp://", "amqps://")):
        return RabbitMQBroker(url)
    raise ValueError(f"Unsupported BROKER_URL: {url!r}")


def get_broker() -> Broker:
    global _broker
    if _broker is None:
        _broker = make_broker()
    return _broker


def set_broker(broker: Broker | None) -> None:
    """Tests swap the broker."""
    global _broker
    _broker = broker

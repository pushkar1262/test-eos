"""REQ-010: Kafka between the Backend API and the Communication Service.

`relay` publishes the outbox rows the store writes; `consume` applies the
Communication Service's Twilio events to board-db. An outbox row is stamped
only after Kafka acknowledges it, and a consumed offset is committed only
after its change is in the database, so a crash on either side means a
redelivery rather than a lost event. The store's writes are idempotent, which
makes the redelivery harmless.

    python -m src.events relay
    python -m src.events consume
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import psycopg

from src.db import connect
from src.store import (
    mark_published,
    record_inbound_message,
    unpublished_events,
    update_message_status,
    upsert_conversation,
)

BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
# Topics the Communication Service publishes to and this service consumes.
INBOUND_TOPICS = ["conversation.upserted", "message.received", "message.status"]


def relay_once(db: psycopg.Connection, producer) -> int:
    """Publish one batch of outbox events. Returns how many were published."""
    with db.transaction():
        batch = unpublished_events(db)
        for event in batch:
            producer.produce(event["topic"], key=event["key"], value=json.dumps(event["payload"]))
        # flush() blocks until Kafka has acknowledged the whole batch, and an
        # undelivered one raises before the rows are stamped.
        if producer.flush(30) > 0:
            raise RuntimeError("Kafka did not acknowledge the batch")
        mark_published(db, [event["id"] for event in batch])
    return len(batch)


def handle(db: psycopg.Connection, topic: str, payload: dict) -> None:
    """Apply one Communication Service event to board-db."""
    if topic == "conversation.upserted":
        upsert_conversation(db, payload["connection_id"], payload["external_sid"], payload["title"])
    elif topic == "message.received":
        record_inbound_message(
            db,
            payload["conversation_sid"],
            payload["external_sid"],
            payload["sender"],
            payload["receiver"],
            payload["content"],
            datetime.fromisoformat(payload["sent_at"]),
        )
    elif topic == "message.status":
        update_message_status(db, payload["message_id"], payload["status"])


def relay(db: psycopg.Connection) -> None:
    from confluent_kafka import Producer

    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS, "enable.idempotence": True})
    while True:
        if relay_once(db, producer) == 0:
            time.sleep(1)


def consume(db: psycopg.Connection) -> None:
    from confluent_kafka import Consumer

    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": "backend-api",
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe(INBOUND_TOPICS)
    while True:
        message = consumer.poll(1.0)
        if message is None:
            continue
        if message.error():
            raise RuntimeError(message.error())
        handle(db, message.topic(), json.loads(message.value()))
        consumer.commit(message, asynchronous=False)


if __name__ == "__main__":
    {"relay": relay, "consume": consume}[sys.argv[1]](connect())

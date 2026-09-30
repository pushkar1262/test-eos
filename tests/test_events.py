"""REQ-010 without a broker: the relay against a fake producer, the consumer's handler directly."""

import pytest

from src.db import connect, migrate
from src.events import handle, relay_once
from src.store import (
    message_history,
    queue_message,
    record_connection,
    set_conversation_status,
    unpublished_events,
    upsert_conversation,
)


class FakeProducer:
    def __init__(self, undelivered=0):
        self.sent, self.undelivered = [], undelivered

    def produce(self, topic, key, value):
        self.sent.append((topic, key, value))

    def flush(self, timeout):
        return self.undelivered


@pytest.fixture(scope="module")
def db():
    connection = connect()
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean(db):
    db.execute("TRUNCATE connections, conversations, messages, outbox_events")


def conversation(db):
    return upsert_conversation(db, record_connection(db, "AC123").id, "CH1", "Ada")


def test_relay_publishes_and_stamps_the_outbox(db):
    set_conversation_status(db, conversation(db).id, "resolved")
    producer = FakeProducer()
    assert relay_once(db, producer) == 1
    assert producer.sent[0][0] == "conversation.status_changed"
    assert unpublished_events(db) == []


def test_unacknowledged_events_stay_in_the_outbox(db):
    set_conversation_status(db, conversation(db).id, "resolved")
    with pytest.raises(RuntimeError):
        relay_once(db, FakeProducer(undelivered=1))
    assert len(unpublished_events(db)) == 1


def test_new_conversation_event_puts_it_on_the_board(db):
    connection = record_connection(db, "AC123")
    event = {"connection_id": str(connection.id), "external_sid": "CH9", "title": "Grace"}
    handle(db, "conversation.upserted", event)
    handle(db, "conversation.upserted", event)  # redelivered
    rows = db.execute("SELECT title FROM conversations").fetchall()
    assert rows == [{"title": "Grace"}]


def test_inbound_message_is_recorded_once(db):
    convo = conversation(db)
    event = {
        "conversation_sid": "CH1", "external_sid": "SM1", "sender": "+15550002",
        "receiver": "+15550001", "content": "hi", "sent_at": "2026-09-30T10:00:00+00:00",
    }
    handle(db, "message.received", event)
    handle(db, "message.received", event)  # redelivered
    [message] = message_history(db, convo.id)
    assert (message.sender, message.content, message.status) == ("+15550002", "hi", "delivered")


def test_status_event_updates_a_queued_message(db):
    convo = conversation(db)
    message = queue_message(db, convo.id, "+15550001", "+15550002", "hi")
    handle(db, "message.status", {"message_id": str(message.id), "status": "delivered"})
    assert message_history(db, convo.id)[0].status == "delivered"

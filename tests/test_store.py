import psycopg
import pytest

from src.db import connect, migrate
from src.store import (
    InvalidStatus,
    NotConnected,
    conversations_since,
    disconnect,
    mark_published,
    message_history,
    queue_message,
    record_connection,
    set_conversation_status,
    unpublished_events,
    update_message_status,
    upsert_conversation,
)


@pytest.fixture(scope="session")
def db():
    connection = connect()
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean(db):
    db.execute("TRUNCATE connections, conversations, messages, outbox_events")


def conversation(db):
    connection = record_connection(db, "AC123")
    return upsert_conversation(db, connection.id, "CH1", "Ada Lovelace")


def topics(db):
    return [row["topic"] for row in db.execute("SELECT topic FROM outbox_events ORDER BY id")]


def test_migrations_are_idempotent(db):
    assert migrate(db) == []


def test_connect_succeeds(db):
    connection = record_connection(db, "AC123")
    assert connection.state == "connected" and connection.last_error is None


def test_failed_connect_keeps_the_error_and_is_not_connected(db):
    record_connection(db, "AC123")
    failed = record_connection(db, "AC123", error="Authentication failed")
    assert failed.state == "failed"
    assert failed.last_error == "Authentication failed"


def test_disconnect_cancels_in_flight_sends(db):
    convo = conversation(db)
    in_flight = queue_message(db, convo.id, "+15550001", "+15550002", "hi")
    delivered = queue_message(db, convo.id, "+15550001", "+15550002", "earlier")
    update_message_status(db, delivered.id, "delivered")

    assert disconnect(db, convo.connection_id) == 1
    statuses = {m.id: m.status for m in message_history(db, convo.id)}
    assert statuses == {in_flight.id: "canceled", delivered.id: "delivered"}
    assert db.execute("SELECT state FROM connections").fetchone()["state"] == "disconnected"
    # A late delivery report does not resurrect the canceled send.
    assert update_message_status(db, in_flight.id, "sent") is False


def test_no_sends_once_disconnected(db):
    convo = conversation(db)
    disconnect(db, convo.connection_id)
    with pytest.raises(NotConnected):
        queue_message(db, convo.id, "+15550001", "+15550002", "hi")


def test_new_conversations_show_up_in_the_next_poll(db):
    first = conversation(db)
    since = conversations_since(db)[-1].updated_at
    assert conversations_since(db, since) == []
    second = upsert_conversation(db, first.connection_id, "CH2", "Grace Hopper")
    assert [c.id for c in conversations_since(db, since)] == [second.id]


def test_replayed_conversation_event_does_not_duplicate(db):
    first = conversation(db)
    again = upsert_conversation(db, first.connection_id, "CH1", "Ada Lovelace")
    assert again.id == first.id


def test_message_history_has_the_displayed_fields(db):
    convo = conversation(db)
    queue_message(db, convo.id, "+15550001", "+15550002", "hello")
    [message] = message_history(db, convo.id)
    assert (message.sender, message.receiver, message.content, message.status) == (
        "+15550001", "+15550002", "hello", "queued",
    )
    assert message.sent_at is not None


def test_status_changes_between_the_defined_values(db):
    convo = conversation(db)
    assert convo.status == "open"
    assert set_conversation_status(db, convo.id, "resolved").status == "resolved"
    with pytest.raises(InvalidStatus):
        set_conversation_status(db, convo.id, "closed")


def test_the_database_rejects_undefined_statuses(db):
    convo = conversation(db)
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        db.execute("UPDATE conversations SET status = 'closed' WHERE id = %s", (convo.id,))


def test_changes_write_outbox_events_for_the_relay(db):
    convo = conversation(db)
    queue_message(db, convo.id, "+15550001", "+15550002", "hi")
    set_conversation_status(db, convo.id, "in_progress")
    disconnect(db, convo.connection_id)
    assert topics(db) == [
        "message.send_requested",
        "conversation.status_changed",
        "connection.disconnected",
    ]

    with db.transaction():
        batch = unpublished_events(db)
        mark_published(db, [event["id"] for event in batch])
    assert unpublished_events(db) == []

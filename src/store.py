"""Queries the Backend API runs against board-db.

Every change the Communication Service must hear about writes its outbox row
in the same transaction, so a committed change always has an event waiting
for the Kafka relay (REQ-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

CONVERSATION_STATUSES = ("open", "in_progress", "resolved")
# Sends the Communication Service has not finished yet; disconnecting cancels them.
IN_FLIGHT = ("queued", "sending")


class NotConnected(Exception):
    """Raised when a message is queued on a connection that is not connected."""


class InvalidStatus(ValueError):
    """Raised for a conversation status outside Open, In Progress, Resolved."""


@dataclass(frozen=True)
class Connection:
    id: UUID
    account_sid: str
    state: str
    last_error: str | None


@dataclass(frozen=True)
class Conversation:
    id: UUID
    connection_id: UUID
    external_sid: str
    title: str
    status: str
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: UUID
    conversation_id: UUID
    sender: str
    receiver: str
    content: str
    status: str
    sent_at: datetime


def record_connection(
    db: psycopg.Connection, account_sid: str, error: str | None = None
) -> Connection:
    """REQ-001: store the outcome of a connect attempt.

    With an error the row is 'failed' and carries the message to display; it is
    never left 'connected' from an earlier attempt.
    """
    row = db.execute(
        "INSERT INTO connections (account_sid, state, last_error, connected_at)"
        " VALUES (%(sid)s, %(state)s, %(error)s, CASE WHEN %(error)s::text IS NULL THEN now() END)"
        " ON CONFLICT (provider, account_sid) DO UPDATE SET"
        "  state = EXCLUDED.state, last_error = EXCLUDED.last_error,"
        "  connected_at = EXCLUDED.connected_at, disconnected_at = NULL"
        " RETURNING id, account_sid, state, last_error",
        {"sid": account_sid, "state": "failed" if error else "connected", "error": error},
    ).fetchone()
    return Connection(**row)


def current_connection(db: psycopg.Connection) -> Connection | None:
    """The most recent connect attempt, which is what the board shows."""
    row = db.execute(
        "SELECT id, account_sid, state, last_error FROM connections"
        " ORDER BY greatest(created_at, connected_at, disconnected_at) DESC LIMIT 1"
    ).fetchone()
    return Connection(**row) if row else None


def disconnect(db: psycopg.Connection, connection_id: UUID) -> int:
    """REQ-001: mark the connection disconnected and cancel its in-flight sends.

    Returns how many sends were canceled. The Communication Service gets one
    event so it drops anything it already picked up.
    """
    with db.transaction():
        db.execute(
            "UPDATE connections SET state = 'disconnected', last_error = NULL,"
            " disconnected_at = now() WHERE id = %s",
            (connection_id,),
        )
        canceled = db.execute(
            "UPDATE messages m SET status = 'canceled' FROM conversations c"
            " WHERE m.conversation_id = c.id AND c.connection_id = %s"
            " AND m.status = ANY(%s::message_status[])",
            (connection_id, list(IN_FLIGHT)),
        ).rowcount
        _emit(db, "connection.disconnected", connection_id, {"connection_id": str(connection_id)})
    return canceled


def upsert_conversation(
    db: psycopg.Connection, connection_id: UUID, external_sid: str, title: str
) -> Conversation:
    """REQ-002: a conversation reported by the Communication Service.

    Replays of the same event update the one row instead of adding another.
    """
    row = db.execute(
        "INSERT INTO conversations (connection_id, external_sid, title) VALUES (%s, %s, %s)"
        " ON CONFLICT (external_sid) DO UPDATE SET title = EXCLUDED.title, updated_at = now()"
        f" RETURNING {_CONVERSATION}",
        (connection_id, external_sid, title),
    ).fetchone()
    return Conversation(**row)


def conversations_since(db: psycopg.Connection, since: datetime | None = None) -> list[Conversation]:
    """REQ-002: what the board polls for, so new conversations appear without a refresh."""
    rows = db.execute(
        f"SELECT {_CONVERSATION} FROM conversations"
        " WHERE %(since)s::timestamptz IS NULL OR updated_at > %(since)s ORDER BY updated_at",
        {"since": since},
    ).fetchall()
    return [Conversation(**row) for row in rows]


def set_conversation_status(
    db: psycopg.Connection, conversation_id: UUID, status: str
) -> Conversation | None:
    """REQ-005: move a conversation between Open, In Progress and Resolved."""
    if status not in CONVERSATION_STATUSES:
        raise InvalidStatus(status)
    with db.transaction():
        row = db.execute(
            "UPDATE conversations SET status = %s, updated_at = now() WHERE id = %s"
            f" RETURNING {_CONVERSATION}",
            (status, conversation_id),
        ).fetchone()
        if row:
            _emit(db, "conversation.status_changed", conversation_id, {"status": status})
    return Conversation(**row) if row else None


def queue_message(
    db: psycopg.Connection, conversation_id: UUID, sender: str, receiver: str, content: str
) -> Message:
    """Queue an outbound message for the Communication Service to send.

    Only a conversation on a connected connection takes new sends.
    """
    with db.transaction():
        row = db.execute(
            "INSERT INTO messages (conversation_id, sender, receiver, content)"
            " SELECT c.id, %s, %s, %s FROM conversations c"
            " JOIN connections k ON k.id = c.connection_id"
            " WHERE c.id = %s AND k.state = 'connected'"
            f" RETURNING {_MESSAGE}",
            (sender, receiver, content, conversation_id),
        ).fetchone()
        if row is None:
            raise NotConnected(conversation_id)
        _emit(db, "message.send_requested", conversation_id, {"message_id": str(row["id"])})
    return Message(**row)


def update_message_status(db: psycopg.Connection, message_id: UUID, status: str) -> bool:
    """Apply a delivery update from the Communication Service.

    A canceled send stays canceled even if a late 'sent' arrives for it.
    """
    return db.execute(
        "UPDATE messages SET status = %s WHERE id = %s AND status <> 'canceled'",
        (status, message_id),
    ).rowcount > 0


def record_inbound_message(
    db: psycopg.Connection,
    conversation_sid: str,
    external_sid: str,
    sender: str,
    receiver: str,
    content: str,
    sent_at: datetime,
) -> bool:
    """A message received through Twilio. A replayed event inserts nothing."""
    with db.transaction():
        inserted = db.execute(
            "INSERT INTO messages"
            " (conversation_id, external_sid, sender, receiver, content, status, sent_at)"
            " SELECT id, %s, %s, %s, %s, 'delivered', %s FROM conversations WHERE external_sid = %s"
            " ON CONFLICT (external_sid) DO NOTHING",
            (external_sid, sender, receiver, content, sent_at, conversation_sid),
        ).rowcount > 0
        # Bumping updated_at puts the conversation in the board's next poll.
        db.execute(
            "UPDATE conversations SET updated_at = now() WHERE external_sid = %s AND %s",
            (conversation_sid, inserted),
        )
    return inserted


def message_history(db: psycopg.Connection, conversation_id: UUID) -> list[Message]:
    """REQ-003: a conversation's messages with sender, receiver, content, time and status."""
    rows = db.execute(
        f"SELECT {_MESSAGE} FROM messages WHERE conversation_id = %s ORDER BY sent_at, id",
        (conversation_id,),
    ).fetchall()
    return [Message(**row) for row in rows]


def unpublished_events(db: psycopg.Connection, limit: int = 100) -> list[dict]:
    """REQ-010: the relay's next batch, oldest first, locked so two relays split the work."""
    return db.execute(
        "SELECT id, topic, key, payload FROM outbox_events WHERE published_at IS NULL"
        " ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
        (limit,),
    ).fetchall()


def mark_published(db: psycopg.Connection, event_ids: list[int]) -> None:
    db.execute("UPDATE outbox_events SET published_at = now() WHERE id = ANY(%s)", (event_ids,))


_CONVERSATION = "id, connection_id, external_sid, title, status, updated_at"
_MESSAGE = "id, conversation_id, sender, receiver, content, status, sent_at"


def _emit(db: psycopg.Connection, topic: str, key: UUID, payload: dict) -> None:
    db.execute(
        "INSERT INTO outbox_events (topic, key, payload) VALUES (%s, %s, %s)",
        (topic, str(key), Jsonb(payload)),
    )

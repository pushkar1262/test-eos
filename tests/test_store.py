from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
import pytest

from src.db import connect, migrate
from src.store import (
    EmailTaken,
    create_session,
    create_user,
    delete_expired_sessions,
    delete_session,
    session_user,
    verify_password,
)

PASSWORD = "correct-horse"


@pytest.fixture(scope="session")
def db():
    connection = connect()
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean(db):
    db.execute("TRUNCATE users, sessions")


def user(db, name="Ada", email="ada@example.com", password=PASSWORD):
    return create_user(db, name, email, password)


def test_migrations_are_idempotent(db):
    # A second run against an up-to-date database applies nothing.
    assert migrate(db) == []


def test_user_gets_a_uuid_key_and_timestamp(db):
    created = user(db)
    assert isinstance(created.id, UUID)
    assert created.created_at <= datetime.now(timezone.utc)


def test_duplicate_email_is_rejected_by_the_constraint(db):
    user(db)
    with pytest.raises(EmailTaken):
        user(db, email="ADA@example.com")


def test_password_is_stored_as_a_bcrypt_hash(db):
    created = user(db)
    row = db.execute(
        "SELECT password_hash FROM users WHERE id = %s", (created.id,)
    ).fetchone()
    stored = row["password_hash"]
    assert stored.startswith("$2b$")
    assert PASSWORD not in stored
    assert int(stored.split("$")[2]) >= 10


def test_credentials_are_checked_against_the_hash(db):
    created = user(db)
    assert verify_password(db, "ada@example.com", PASSWORD).id == created.id
    assert verify_password(db, "ada@example.com", "wrong-password") is None
    assert verify_password(db, "nobody@example.com", PASSWORD) is None


def test_session_stores_only_the_token_digest(db):
    created = user(db)
    session, token = create_session(db, created.id)
    row = db.execute(
        "SELECT token_hash FROM sessions WHERE id = %s", (session.id,)
    ).fetchone()
    assert row["token_hash"] != token
    assert session_user(db, token).id == created.id


def test_token_hash_is_unique(db):
    created = user(db)
    _, token = create_session(db, created.id)
    duplicate = db.execute(
        "SELECT token_hash FROM sessions LIMIT 1"
    ).fetchone()["token_hash"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at)"
            " VALUES (%s, %s, now() + interval '1 hour')",
            (created.id, duplicate),
        )
    assert session_user(db, token).id == created.id


def test_session_requires_a_real_user(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at)"
            " VALUES (gen_random_uuid(), 'orphan', now() + interval '1 hour')"
        )


def test_deleting_a_user_deletes_their_sessions(db):
    created = user(db)
    create_session(db, created.id)
    db.execute("DELETE FROM users WHERE id = %s", (created.id,))
    assert db.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"] == 0


def test_logout_deletes_the_session(db):
    created = user(db)
    _, token = create_session(db, created.id)
    assert delete_session(db, token) is True
    assert session_user(db, token) is None
    assert delete_session(db, token) is False


def test_expired_sessions_buy_nothing_and_are_cleaned_up(db):
    created = user(db)
    _, token = create_session(db, created.id)
    db.execute(
        "UPDATE sessions SET expires_at = %s",
        (datetime.now(timezone.utc) - timedelta(seconds=1),),
    )
    assert session_user(db, token) is None
    assert delete_expired_sessions(db) == 0  # session_user already removed it

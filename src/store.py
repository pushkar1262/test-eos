"""Queries auth-api runs against auth-db.

Every write that a constraint can reject goes through here, so the callers see
a named error (EmailTaken) instead of a driver-level IntegrityError. Raw
passwords and raw session tokens are arguments to these functions and nothing
else: what reaches a column is always a hash.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import psycopg

# REQ-003. 10 is the floor the requirement sets; a lower value is a
# misconfiguration worth failing on rather than quietly hashing weakly.
MIN_BCRYPT_ROUNDS = 10
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "72"))


class EmailTaken(Exception):
    """Raised when the users.email unique constraint rejects an insert."""


@dataclass(frozen=True)
class User:
    id: UUID
    name: str
    email: str
    created_at: datetime


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
    expires_at: datetime


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    """REQ-003: the only place a raw password becomes a stored value."""
    if BCRYPT_ROUNDS < MIN_BCRYPT_ROUNDS:
        raise ValueError(f"BCRYPT_ROUNDS must be at least {MIN_BCRYPT_ROUNDS}")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def create_user(db: psycopg.Connection, name: str, email: str, password: str) -> User:
    """REQ-002: insert a user, or raise EmailTaken.

    The uniqueness is decided by the constraint, not by a preceding SELECT, so
    two simultaneous signups for the same address cannot both succeed.
    """
    try:
        row = db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)"
            " RETURNING id, name, email, created_at",
            (name.strip(), normalize_email(email), hash_password(password)),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise EmailTaken(email) from None
    return User(**row)


def verify_password(db: psycopg.Connection, email: str, password: str) -> User | None:
    """Return the user if the password matches the stored hash, else None.

    An unknown email still pays for one bcrypt check, so the response time does
    not say whether the account exists.
    """
    row = db.execute(
        "SELECT id, name, email, created_at, password_hash FROM users WHERE email = %s",
        (normalize_email(email),),
    ).fetchone()
    stored = row["password_hash"] if row else _unusable_hash()
    matched = bcrypt.checkpw(password.encode(), stored.encode())
    if row is None or not matched:
        return None
    del row["password_hash"]
    return User(**row)


def create_session(db: psycopg.Connection, user_id: UUID) -> tuple[Session, str]:
    """REQ-005: mint a session and return it with the token for the cookie.

    Only the SHA-256 digest is stored, so the sessions table cannot be read out
    and replayed as cookies. The caller is what sets HttpOnly and Secure.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    row = db.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)"
        " RETURNING id, user_id, expires_at",
        (user_id, _digest(token), expires_at),
    ).fetchone()
    return Session(**row), token


def session_user(db: psycopg.Connection, token: str | None) -> User | None:
    """Return the user behind a session token, or None if it buys nothing.

    An expired row is deleted on the way out rather than left to accumulate.
    """
    if not token:
        return None
    row = db.execute(
        "SELECT u.id, u.name, u.email, u.created_at, s.expires_at"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = %s",
        (_digest(token),),
    ).fetchone()
    if row is None:
        return None
    if row.pop("expires_at") <= datetime.now(timezone.utc):
        delete_session(db, token)
        return None
    return User(**row)


def delete_session(db: psycopg.Connection, token: str | None) -> bool:
    """REQ-007: drop the session row. Logging out twice is not an error."""
    if not token:
        return False
    result = db.execute("DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))
    return result.rowcount > 0


def delete_expired_sessions(db: psycopg.Connection) -> int:
    """Housekeeping for rows whose owner never logged out."""
    return db.execute("DELETE FROM sessions WHERE expires_at <= now()").rowcount


def _unusable_hash() -> str:
    return bcrypt.hashpw(b"no-such-user", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

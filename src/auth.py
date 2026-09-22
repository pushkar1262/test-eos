"""Account storage, password hashing and session issuance for auth-api.

Backed by the auth-db Postgres schema: one `users` table and one `sessions`
table. Session tokens are random and stored as SHA-256 digests, so a dump of
the sessions table cannot be replayed as cookies.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/authdb"
)
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "72"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
"""


class AuthError(Exception):
    """Raised when a request cannot be authenticated or an email is taken."""


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str
    created_at: datetime


def connect(dsn: str = DATABASE_URL) -> psycopg.Connection:
    db = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    db.execute(SCHEMA)
    return db


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode(), salt).decode()


def create_user(db: psycopg.Connection, name: str, email: str, password: str) -> User:
    """Insert a user, or raise AuthError if the email is already registered."""
    if not name.strip():
        raise AuthError("name is required")
    password_hash = hash_password(password)
    try:
        row = db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)"
            " RETURNING id, name, email, created_at",
            (name.strip(), normalize_email(email), password_hash),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise AuthError("email already registered") from None
    return User(**row)


def issue_session(db: psycopg.Connection, email: str, password: str) -> str:
    """Verify credentials and return a new session token.

    The same AuthError is raised for an unknown email and a wrong password, and
    an unknown email still pays for one bcrypt check, so neither the message nor
    the timing says whether the account exists.
    """
    row = db.execute(
        "SELECT id, password_hash FROM users WHERE email = %s", (normalize_email(email),)
    ).fetchone()
    stored = row["password_hash"] if row else _dummy_hash()
    matched = bcrypt.checkpw(password.encode(), stored.encode())
    if row is None or not matched:
        raise AuthError("invalid email or password")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_digest(token), row["id"], expires_at),
    )
    return token


def resolve_session(db: psycopg.Connection, token: str | None) -> User:
    """Return the user behind a session token, or raise AuthError.

    An expired row is deleted on the way out rather than left to accumulate.
    """
    if not token:
        raise AuthError("not authenticated")
    row = db.execute(
        "SELECT u.id, u.name, u.email, u.created_at, s.expires_at"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = %s",
        (_digest(token),),
    ).fetchone()
    if row is None:
        raise AuthError("not authenticated")
    if row.pop("expires_at") <= datetime.now(timezone.utc):
        revoke_session(db, token)
        raise AuthError("session expired")
    return User(**row)


def revoke_session(db: psycopg.Connection, token: str | None) -> None:
    """Delete the session row. Logging out twice is not an error."""
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))


def _dummy_hash() -> str:
    """A throwaway hash to check unknown emails against, at the real cost."""
    return bcrypt.hashpw(b"no-such-user", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

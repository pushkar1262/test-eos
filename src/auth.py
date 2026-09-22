"""Account storage, password hashing and session issuance for auth-api.

Backed by the auth-db schema: one `users` table and one `sessions` table.
Session tokens are random and stored as SHA-256 digests, so a dump of the
sessions table cannot be replayed as cookies.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt

BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "72"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
"""


class AuthError(Exception):
    """Raised when a request cannot be authenticated or an email is taken."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    created_at: str


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode(), salt).decode()


def create_user(db: sqlite3.Connection, email: str, password: str) -> User:
    """Insert a user, or raise AuthError if the email is already registered."""
    email = normalize_email(email)
    password_hash = hash_password(password)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        cur = db.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, password_hash, created_at),
        )
    except sqlite3.IntegrityError:
        raise AuthError("email already registered") from None
    db.commit()
    return User(id=cur.lastrowid, email=email, created_at=created_at)


def issue_session(db: sqlite3.Connection, email: str, password: str) -> str:
    """Verify credentials and return a new session token.

    The same AuthError is raised for an unknown email and a wrong password, and
    an unknown email still pays for one bcrypt check, so neither the message nor
    the timing says whether the account exists.
    """
    row = db.execute(
        "SELECT id, password_hash FROM users WHERE email = ?", (normalize_email(email),)
    ).fetchone()
    stored = row["password_hash"] if row else _dummy_hash()
    matched = bcrypt.checkpw(password.encode(), stored.encode())
    if row is None or not matched:
        raise AuthError("invalid email or password")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_digest(token), row["id"], expires_at.isoformat()),
    )
    db.commit()
    return token


def resolve_session(db: sqlite3.Connection, token: str | None) -> User:
    """Return the user behind a session token, or raise AuthError.

    An expired row is deleted on the way out rather than left to accumulate.
    """
    if not token:
        raise AuthError("not authenticated")
    row = db.execute(
        "SELECT u.id, u.email, u.created_at, s.expires_at"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ?",
        (_digest(token),),
    ).fetchone()
    if row is None:
        raise AuthError("not authenticated")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        revoke_session(db, token)
        raise AuthError("session expired")
    return User(id=row["id"], email=row["email"], created_at=row["created_at"])


def revoke_session(db: sqlite3.Connection, token: str | None) -> None:
    """Delete the session row. Logging out twice is not an error."""
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
        db.commit()


def _dummy_hash() -> str:
    """A throwaway hash to check unknown emails against, at the real cost."""
    return bcrypt.hashpw(b"no-such-user", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

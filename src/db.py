"""Connection handling and migrations for auth-db.

Migrations are plain .sql files applied in filename order and recorded in
schema_migrations, so running this against an up-to-date database is a no-op
and every environment gets the same schema from the same files.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/authdb"
)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(dsn: str = DATABASE_URL) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def migrate(db: psycopg.Connection) -> list[str]:
    """Apply any migrations this database has not seen. Returns their names."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    applied = {
        row["name"] for row in db.execute("SELECT name FROM schema_migrations").fetchall()
    }

    ran = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        # Each migration and its bookkeeping row commit together, so a failure
        # halfway through leaves the file un-applied rather than half-applied.
        with db.transaction():
            db.execute(path.read_text())
            db.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        ran.append(path.name)
    return ran


if __name__ == "__main__":
    with connect() as connection:
        for name in migrate(connection) or ["(nothing to do)"]:
            print(f"applied {name}")

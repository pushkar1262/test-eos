-- ENT-001 User. One row per registration. The email is the natural key the
-- signup path checks against, so the uniqueness is a database constraint and
-- not an application-level SELECT that two concurrent signups could both pass.
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Emails are stored already lowercased; this index makes that a rule the
-- database keeps rather than a habit the callers have.
CREATE UNIQUE INDEX users_email_lower_idx ON users (lower(email));

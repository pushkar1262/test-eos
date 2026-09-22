-- ENT-002 Session. REL-001: many sessions to one user, via user_id. Deleting a
-- user takes their sessions with it, so no row can outlive the account it
-- authenticates.
CREATE TABLE sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Logout and "sign out everywhere" both delete by user_id.
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

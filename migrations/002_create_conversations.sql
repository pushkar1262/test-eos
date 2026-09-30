-- REQ-002 / REQ-005. The enum is what makes Open, In Progress and Resolved the
-- only statuses there are; anything else is rejected by the database.
CREATE TYPE conversation_status AS ENUM ('open', 'in_progress', 'resolved');

CREATE TABLE conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    external_sid  TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    status        conversation_status NOT NULL DEFAULT 'open',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The board polls for conversations changed since its last fetch, so a newly
-- connected one shows up without a page refresh.
CREATE INDEX conversations_updated_at_idx ON conversations (updated_at);

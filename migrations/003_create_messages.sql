-- REQ-003. Every column the message view shows: sender, receiver, content,
-- timestamp and status.
CREATE TYPE message_status AS ENUM
    ('queued', 'sending', 'sent', 'delivered', 'failed', 'canceled');

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    external_sid    TEXT UNIQUE,
    sender          TEXT NOT NULL,
    receiver        TEXT NOT NULL,
    content         TEXT NOT NULL,
    status          message_status NOT NULL DEFAULT 'queued',
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Message history is read one conversation at a time, oldest first.
CREATE INDEX messages_conversation_sent_at_idx ON messages (conversation_id, sent_at);

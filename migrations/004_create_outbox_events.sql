-- REQ-010. Transactional outbox for the Kafka topics between the Backend API
-- and the Communication Service: an event is written in the same transaction
-- as the change it describes, and a relay publishes it and stamps
-- published_at. A crash between commit and publish delays the event instead
-- of losing it.
CREATE TABLE outbox_events (
    id           BIGSERIAL PRIMARY KEY,
    topic        TEXT NOT NULL,
    key          TEXT NOT NULL,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);

CREATE INDEX outbox_events_unpublished_idx ON outbox_events (id) WHERE published_at IS NULL;

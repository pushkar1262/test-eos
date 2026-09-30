-- REQ-001 Sprintle/Twilio connection. The board is connected only while a row
-- here says 'connected'; a failed attempt lands in 'failed' with the error the
-- UI shows, so it can never be mistaken for a connected board.
CREATE TYPE connection_state AS ENUM ('connected', 'disconnected', 'failed');

CREATE TABLE connections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        TEXT NOT NULL DEFAULT 'twilio',
    account_sid     TEXT NOT NULL,
    state           connection_state NOT NULL,
    last_error      TEXT,
    connected_at    TIMESTAMPTZ,
    disconnected_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, account_sid),
    CHECK ((state = 'failed') = (last_error IS NOT NULL))
);

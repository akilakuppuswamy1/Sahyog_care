BEGIN;

ALTER TABLE w1_request ADD COLUMN IF NOT EXISTS fingerprint text;

CREATE TABLE api_rate_bucket (
    bucket_id text PRIMARY KEY,
    tokens double precision NOT NULL CHECK (tokens >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_replay_request (
    action text NOT NULL CHECK (action IN ('w1_resume', 'upload')),
    owner_id text NOT NULL,
    idempotency_key text NOT NULL,
    identity text NOT NULL,
    fingerprint text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'complete')),
    response jsonb,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (action, owner_id, idempotency_key),
    CHECK ((status = 'complete' AND response IS NOT NULL AND lease_until IS NULL)
        OR (status = 'running' AND response IS NULL AND lease_until IS NOT NULL))
);

CREATE INDEX api_replay_request_lease_idx
    ON api_replay_request (lease_until) WHERE status = 'running';

COMMIT;

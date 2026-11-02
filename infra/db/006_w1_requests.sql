-- Reserve triage identity before any model call or checkpoint exists.
BEGIN;
CREATE TABLE IF NOT EXISTS w1_request (
 user_id text NOT NULL,
 idempotency_key text NOT NULL,
 thread_id text NOT NULL UNIQUE,
 running boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (user_id, idempotency_key)
);
COMMIT;

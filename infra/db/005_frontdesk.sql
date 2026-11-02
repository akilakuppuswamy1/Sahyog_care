-- Hand-offs remain immutable audit events; handling state and write replays live separately.
BEGIN;
CREATE TABLE IF NOT EXISTS frontdesk_handling (
 event_id bigint PRIMARY KEY REFERENCES audit_event,
 status text NOT NULL DEFAULT 'handled' CHECK (status = 'handled'),
 handled_by text NOT NULL,
 handled_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS frontdesk_replay (
 staff_id text NOT NULL,
 idempotency_key text NOT NULL,
 fingerprint text NOT NULL,
 result jsonb NOT NULL,
 PRIMARY KEY (staff_id, idempotency_key)
);
COMMIT;

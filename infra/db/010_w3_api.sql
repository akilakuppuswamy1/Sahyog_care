BEGIN;

ALTER TABLE note_draft ADD COLUMN IF NOT EXISTS initial_soap_json jsonb;

CREATE TABLE w3_request (
    doctor_id       text NOT NULL REFERENCES doctor,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    fingerprint     text NOT NULL,
    draft_id        uuid NOT NULL,
    status          text NOT NULL DEFAULT 'running' CHECK (status IN ('running','complete')),
    response        jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doctor_id, idempotency_key),
    CHECK ((status = 'complete') = (response IS NOT NULL))
);

COMMIT;

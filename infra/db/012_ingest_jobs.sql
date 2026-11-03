BEGIN;
ALTER TABLE document ADD COLUMN IF NOT EXISTS content_hash text;
CREATE UNIQUE INDEX document_owner_hash ON document(patient_id, content_hash) WHERE content_hash IS NOT NULL;
CREATE TABLE ingest_job (
 job_id uuid PRIMARY KEY,
 doc_id text NOT NULL,
 patient_id text NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
 last_error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz,
 available_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (doc_id, patient_id) REFERENCES document(doc_id, patient_id),
 UNIQUE(doc_id)
);
CREATE INDEX ingest_job_ready ON ingest_job(available_at, created_at) WHERE status IN ('queued','running');
COMMIT;

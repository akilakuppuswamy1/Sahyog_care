BEGIN;

ALTER TABLE document
    ADD COLUMN source text NOT NULL DEFAULT 'hospital'
    CHECK (source IN ('hospital', 'patient_upload'));

UPDATE document
   SET source = 'patient_upload'
 WHERE uri LIKE 'file:%/data/uploads/%';

CREATE TABLE document_phi_token_map (
    doc_id text PRIMARY KEY REFERENCES document (doc_id) ON DELETE CASCADE,
    token_map jsonb NOT NULL
);

COMMIT;

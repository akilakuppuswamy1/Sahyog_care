-- W2 My records: hybrid retrieval support and ingest bookkeeping.
-- Apply to an existing database:
--   docker compose -f infra/docker-compose.yml exec -w /repo db psql -U sahyog -d sahyog -v ON_ERROR_STOP=1 -f infra/db/004_w2_records.sql

BEGIN;

ALTER TABLE document ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE document ADD COLUMN IF NOT EXISTS report_date date;
ALTER TABLE document ADD COLUMN IF NOT EXISTS error text;

ALTER TABLE doc_chunk ADD COLUMN IF NOT EXISTS content_hash text;      -- embeddings cached by content hash
ALTER TABLE doc_chunk ADD COLUMN IF NOT EXISTS flags text[] NOT NULL DEFAULT '{}';   -- e.g. {injection}
-- Keyword half of hybrid search. 'simple' = no stemming, no English stop words: nothing assumes English.
ALTER TABLE doc_chunk ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;

CREATE INDEX IF NOT EXISTS doc_chunk_tsv_idx ON doc_chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS doc_chunk_hash_idx ON doc_chunk (content_hash);
-- Vector half. Every query also filters patient_id, so this index only speeds up the candidate search.
CREATE INDEX IF NOT EXISTS doc_chunk_embedding_idx ON doc_chunk USING hnsw (embedding vector_cosine_ops);

COMMIT;

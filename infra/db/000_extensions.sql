-- Extensions. Kept in their own file so the schema tests can run on Postgres without pgvector.
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector: doc_chunk.embedding
-- gen_random_uuid() and sha256() are built into Postgres 13+, so no pgcrypto is needed.

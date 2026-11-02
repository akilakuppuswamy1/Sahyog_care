BEGIN;

ALTER TABLE note_draft DROP CONSTRAINT IF EXISTS note_draft_encounter_id_version_key;
CREATE UNIQUE INDEX IF NOT EXISTS note_draft_active_version_idx
    ON note_draft (encounter_id, version) WHERE status = 'draft';

COMMIT;

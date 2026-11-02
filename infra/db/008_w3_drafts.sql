BEGIN;

CREATE TABLE note_draft (
    draft_id          uuid PRIMARY KEY,
    encounter_id      text NOT NULL REFERENCES encounter,
    doctor_id         text NOT NULL REFERENCES doctor,
    version           int NOT NULL CHECK (version >= 1),
    soap_json         jsonb NOT NULL CHECK (soap_json ?& ARRAY['S','O','A','P']),
    flags             jsonb NOT NULL DEFAULT '[]'::jsonb,
    status            text NOT NULL CHECK (status IN ('draft','parked','signed','rejected')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    review_started_at timestamptz,
    signed_note_id    uuid REFERENCES clinical_note,
    UNIQUE (encounter_id, version),
    CHECK ((status = 'signed') = (signed_note_id IS NOT NULL))
);
CREATE INDEX note_draft_review_queue_idx ON note_draft (status, review_started_at)
    WHERE status IN ('draft','parked');

CREATE TABLE note_edit (
    edit_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id   uuid NOT NULL REFERENCES note_draft,
    field      text NOT NULL,
    "before"   jsonb,
    "after"    jsonb NOT NULL,
    edited_by  text NOT NULL REFERENCES doctor,
    edited_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX note_edit_draft_idx ON note_edit (draft_id, edited_at);

CREATE FUNCTION clinical_note_treating_doctor()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    treating_doctor text;
BEGIN
    SELECT doctor_id INTO treating_doctor
      FROM encounter
     WHERE encounter_id = NEW.encounter_id
     FOR SHARE;
    IF treating_doctor IS NULL OR NEW.doctor_id <> treating_doctor OR NEW.signed_by <> treating_doctor THEN
        RAISE EXCEPTION 'only the treating doctor may sign an encounter note'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER clinical_note_treating_doctor
    BEFORE INSERT ON clinical_note
    FOR EACH ROW EXECUTE FUNCTION clinical_note_treating_doctor();

COMMIT;

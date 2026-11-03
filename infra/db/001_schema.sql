-- Sahyog Care Companion: entity model (deck p.18) plus clinic and audit_event (deck p.19).
-- 14 tables. Every rule that protects a patient lives here as a constraint or trigger,
-- not in a prompt (deck p.31: "a prompt can be argued with; a SQL filter cannot").
--
-- Conventions
--   * ids are text (natural/generated keys from the data layer), uuids for runtime rows
--   * language columns are BCP-47 text, never an enum: nothing may assume English (deck p.4)
--   * synthetic = true on every generated row (deck p.20, rule 6)

BEGIN;

-- ---------------------------------------------------------------- reference
CREATE TABLE clinic (
    clinic_id     text PRIMARY KEY,
    name          text NOT NULL,
    city          text NOT NULL CHECK (city IN ('Pune', 'Hyderabad')),
    kind          text NOT NULL CHECK (kind IN ('hospital', 'clinic')),
    district      text NOT NULL,                 -- quasi-identifier treatment: district, not pincode
    opens_at      time NOT NULL,
    closes_at     time NOT NULL,
    has_pharmacy  boolean NOT NULL DEFAULT true,
    synthetic     boolean NOT NULL DEFAULT true,
    CHECK (closes_at > opens_at)
);

CREATE TABLE specialty (
    specialty_id    text PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    red_flag_rules  jsonb NOT NULL DEFAULT '[]'::jsonb  -- specialty-specific extras; global rules live in code
);

CREATE TABLE doctor (
    doctor_id        text PRIMARY KEY,
    name             text NOT NULL,
    gender           text NOT NULL CHECK (gender IN ('F', 'M')),   -- kept for the bias audit (deck p.20)
    specialty_id     text NOT NULL REFERENCES specialty,
    clinic_id        text NOT NULL REFERENCES clinic,
    languages        text[] NOT NULL CHECK (cardinality(languages) >= 1),
    years_practice   int  NOT NULL CHECK (years_practice BETWEEN 0 AND 60),
    synthetic        boolean NOT NULL DEFAULT true
);
CREATE INDEX doctor_specialty_idx ON doctor (specialty_id, clinic_id);

-- ---------------------------------------------------------------- people
CREATE TABLE patient (
    patient_id   text PRIMARY KEY,
    abha_id      text UNIQUE,                    -- Architect-tier link; nullable in Core
    name         text NOT NULL,                  -- direct identifier: never leaves the boundary
    dob          date NOT NULL,                  -- quasi-identifier: age band only on egress
    phone        text,                           -- direct identifier
    language     text NOT NULL DEFAULT 'en',     -- BCP-47
    synthetic    boolean NOT NULL DEFAULT true
);

-- Consent is a row, not a checkbox (deck p.5, p.33).
CREATE TABLE consent (
    consent_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id   text NOT NULL REFERENCES patient,
    grantee_id   text NOT NULL,                  -- caregiver's identity subject
    scope        text[] NOT NULL CHECK (scope <@ ARRAY['records','appointments','signed_notes']::text[]
                                        AND cardinality(scope) >= 1),
    purpose      text NOT NULL,
    granted_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    CHECK (expires_at > granted_at),
    CHECK (grantee_id <> patient_id)
);
CREATE INDEX consent_lookup_idx ON consent (grantee_id, patient_id);

-- The one place an access decision is made. Called on every caregiver read.
CREATE FUNCTION consent_valid(p_grantee text, p_patient text, p_scope text, p_at timestamptz DEFAULT now())
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM consent
        WHERE grantee_id = p_grantee AND patient_id = p_patient
          AND p_scope = ANY (scope)
          AND revoked_at IS NULL
          AND p_at < expires_at
    );
$$;

-- ---------------------------------------------------------------- scheduling
CREATE TABLE slot (
    slot_id     text PRIMARY KEY,
    doctor_id   text NOT NULL REFERENCES doctor,
    clinic_id   text NOT NULL REFERENCES clinic,
    starts_at   timestamptz NOT NULL,
    capacity    int NOT NULL DEFAULT 1 CHECK (capacity >= 1),   -- capacity, not free/taken (D3 A05)
    booked      int NOT NULL DEFAULT 0,
    synthetic   boolean NOT NULL DEFAULT true,
    CHECK (booked BETWEEN 0 AND capacity),                       -- double-booking is impossible
    UNIQUE (doctor_id, starts_at)
);
CREATE INDEX slot_next_free_idx ON slot (doctor_id, starts_at) WHERE booked < capacity;

CREATE TABLE appointment (
    appt_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id       text NOT NULL REFERENCES patient,
    slot_id          text NOT NULL REFERENCES slot,
    status           text NOT NULL DEFAULT 'booked'
                     CHECK (status IN ('booked','cancelled','attended','no_show','overridden')),
    booked_by        text NOT NULL CHECK (booked_by IN ('assistant','front_desk','patient')),
    idempotency_key  text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (patient_id, idempotency_key)                         -- retries return the same row
);
CREATE INDEX appointment_slot_idx ON appointment (slot_id);
-- One patient cannot hold two live bookings in the same slot.
CREATE UNIQUE INDEX appointment_one_per_slot ON appointment (patient_id, slot_id) WHERE status = 'booked';

-- Booking is one transaction (deck p.29, message 7). Idempotent: same key -> same appt_id.
CREATE FUNCTION book_slot(p_patient text, p_slot text, p_key text, p_by text DEFAULT 'assistant')
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_appt uuid;
BEGIN
    SELECT appt_id INTO v_appt FROM appointment
     WHERE patient_id = p_patient AND idempotency_key = p_key;
    IF FOUND THEN
        RETURN v_appt;                                           -- a retry, not a second booking
    END IF;

    UPDATE slot SET booked = booked + 1
     WHERE slot_id = p_slot AND booked < capacity;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'slot % is full or does not exist', p_slot USING ERRCODE = 'P0001';
    END IF;

    INSERT INTO appointment (patient_id, slot_id, booked_by, idempotency_key)
    VALUES (p_patient, p_slot, p_by, p_key)
    RETURNING appt_id INTO v_appt;
    RETURN v_appt;
END;
$$;

-- Front desk can cancel or override any booking; the seat is released in the same transaction.
CREATE FUNCTION release_appointment(p_appt uuid, p_new_status text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF p_new_status NOT IN ('cancelled', 'overridden') THEN
        RAISE EXCEPTION 'release_appointment only cancels or overrides';
    END IF;
    UPDATE slot s SET booked = booked - 1
      FROM appointment a
     WHERE a.appt_id = p_appt AND a.status = 'booked' AND s.slot_id = a.slot_id;
    UPDATE appointment SET status = p_new_status WHERE appt_id = p_appt AND status = 'booked';
END;
$$;

-- ---------------------------------------------------------------- consultation
CREATE TABLE encounter (
    encounter_id    text PRIMARY KEY,
    appt_id         uuid UNIQUE REFERENCES appointment,  -- one-to-one; null for imported history
    patient_id      text NOT NULL REFERENCES patient,
    doctor_id       text REFERENCES doctor,
    clinic_id       text REFERENCES clinic,
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    transcript_uri  text,                                -- transcript kept; audio deleted in 24 h
    synthetic       boolean NOT NULL DEFAULT true,
    CHECK (ended_at IS NULL OR ended_at >= started_at)   -- the "discharge before admission" fix
);

-- Only signed notes are persisted. Drafts live in the checkpointer (deck p.5: "an unsigned
-- note is a draft by schema"). Signed notes are immutable; an edit is a new version.
CREATE TABLE clinical_note (
    note_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    encounter_id   text NOT NULL REFERENCES encounter,
    doctor_id      text NOT NULL REFERENCES doctor,
    version        int  NOT NULL CHECK (version >= 1),
    soap_json      jsonb NOT NULL CHECK (soap_json ?& ARRAY['S','O','A','P']),
    edit_history   jsonb NOT NULL DEFAULT '[]'::jsonb,
    signed_by      text NOT NULL,
    signed_at      timestamptz NOT NULL,
    content_hash   text NOT NULL,
    supersedes     uuid REFERENCES clinical_note,
    UNIQUE (encounter_id, version),
    CHECK (signed_by = doctor_id),                       -- the treating doctor signs, nobody else
    CHECK ((version = 1) = (supersedes IS NULL))
);

CREATE FUNCTION clinical_note_set_hash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.content_hash := encode(sha256(convert_to(NEW.soap_json::text, 'UTF8')), 'hex');
    RETURN NEW;
END;
$$;
CREATE TRIGGER clinical_note_hash BEFORE INSERT ON clinical_note
    FOR EACH ROW EXECUTE FUNCTION clinical_note_set_hash();

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: rows are immutable', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'P0001';  -- raise_exception. (P0002 is no_data_found: wrong class, caught by the DB tests)
END;
$$;
CREATE TRIGGER clinical_note_immutable BEFORE UPDATE OR DELETE ON clinical_note
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- ---------------------------------------------------------------- pharmacy
CREATE TABLE medicine_stock (
    medicine_id   text NOT NULL,
    clinic_id     text NOT NULL REFERENCES clinic,
    name          text NOT NULL,                 -- brand name from the A-Z formulary
    composition   text NOT NULL,                 -- grouping key for substitutes
    price_inr     numeric(10,2) CHECK (price_inr >= 0),
    qty           int NOT NULL CHECK (qty >= 0),
    as_of         timestamptz NOT NULL,          -- staleness is shown, never hidden (D3 A02)
    source        text NOT NULL CHECK (source IN ('csv','api')),
    PRIMARY KEY (medicine_id, clinic_id)
);
CREATE INDEX medicine_stock_composition_idx ON medicine_stock (clinic_id, composition);

CREATE TABLE prescription (
    rx_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id        uuid NOT NULL REFERENCES clinical_note,
    medicine_id    text,                         -- null until the doctor names a molecule
    medicine_text  text NOT NULL,                -- as the doctor said it
    dose           text,
    substitute_ok  boolean NOT NULL DEFAULT false   -- pharmacist decides; software never substitutes
);

-- ---------------------------------------------------------------- records
CREATE TABLE document (
    doc_id        text PRIMARY KEY,
    patient_id    text NOT NULL REFERENCES patient,
    type          text NOT NULL CHECK (type IN ('lab','radiology','discharge','prescription','other')),
    uri           text NOT NULL,
    language      text NOT NULL DEFAULT 'en',
    uploaded_at   timestamptz NOT NULL DEFAULT now(),
    ingest_status text NOT NULL DEFAULT 'queued'
                  CHECK (ingest_status IN ('queued','parsing','indexed','failed')),  -- E3-1
    phi_status    text NOT NULL DEFAULT 'untagged' CHECK (phi_status IN ('untagged','tagged')),
    synthetic     boolean NOT NULL DEFAULT true
);
CREATE INDEX document_patient_idx ON document (patient_id);

-- patient_id is copied onto every chunk so the retrieval filter is a plain WHERE clause.
-- The composite FK guarantees the copy can never disagree with the parent document.
ALTER TABLE document ADD CONSTRAINT document_id_patient_uq UNIQUE (doc_id, patient_id);
CREATE TABLE doc_chunk (
    chunk_id    text PRIMARY KEY,
    doc_id      text NOT NULL,
    patient_id  text NOT NULL,
    page        int  NOT NULL CHECK (page >= 1),
    section     text,
    text        text NOT NULL,
    embedding   vector(1024),
    FOREIGN KEY (doc_id, patient_id) REFERENCES document (doc_id, patient_id) ON DELETE CASCADE
);
CREATE INDEX doc_chunk_patient_idx ON doc_chunk (patient_id);

-- ---------------------------------------------------------------- audit
-- One row per read or write of any table (deck p.19). Append-only.
CREATE TABLE audit_event (
    event_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT now(),
    actor_id       text NOT NULL,
    actor_role     text NOT NULL CHECK (actor_role IN
                   ('patient','caregiver','doctor','front_desk','ops','pharmacist','compliance','system')),
    action         text NOT NULL CHECK (action IN ('read','write','delete','sign','override','escalate')),
    resource_type  text NOT NULL,
    resource_id    text NOT NULL,
    patient_id     text,                         -- null for operational rows
    purpose        text NOT NULL,
    trace_id       text,                         -- links to the Langfuse trace (deck p.47)
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_event_patient_idx ON audit_event (patient_id, at);
CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- Kavita's one query (E6-1): "who saw this record?"
CREATE VIEW who_saw_record AS
    SELECT patient_id, at, actor_id, actor_role, purpose, resource_type, resource_id, trace_id
      FROM audit_event
     WHERE action = 'read' AND patient_id IS NOT NULL;

COMMIT;

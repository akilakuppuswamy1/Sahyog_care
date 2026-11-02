# Entity model — migrations 001–007

Derived from the checked-in SQL, including all 18 persistent application tables and columns.
Migration 002 uses temporary staging tables `_clinic` and `_spec` for loading; these are not persistent entities.
Migration 003 replaces a trigger function; 004 adds document/chunk columns and indexes; 005–006 add three tables;
007 adds the PHI-free `trace_span` table and audit trace-context trigger.
LangGraph creates its own checkpoint tables through `PostgresSaver.setup()`; those are outside migrations 001–007.

```mermaid
erDiagram
    clinic {
        text clinic_id PK
        text name
        text city
        text kind
        text district
        time opens_at
        time closes_at
        boolean has_pharmacy
        boolean synthetic
    }
    specialty {
        text specialty_id PK
        text name
        jsonb red_flag_rules
    }
    doctor {
        text doctor_id PK
        text name
        text gender
        text specialty_id FK
        text clinic_id FK
        text_array languages
        int years_practice
        boolean synthetic
    }
    patient {
        text patient_id PK
        text abha_id
        text name
        date dob
        text phone
        text language
        boolean synthetic
    }
    consent {
        uuid consent_id PK
        text patient_id FK
        text grantee_id
        text_array scope
        text purpose
        timestamptz granted_at
        timestamptz expires_at
        timestamptz revoked_at
    }
    slot {
        text slot_id PK
        text doctor_id FK
        text clinic_id FK
        timestamptz starts_at
        int capacity
        int booked
        boolean synthetic
    }
    appointment {
        uuid appt_id PK
        text patient_id FK
        text slot_id FK
        text status
        text booked_by
        text idempotency_key
        timestamptz created_at
    }
    encounter {
        text encounter_id PK
        uuid appt_id FK
        text patient_id FK
        text doctor_id FK
        text clinic_id FK
        timestamptz started_at
        timestamptz ended_at
        text transcript_uri
        boolean synthetic
    }
    clinical_note {
        uuid note_id PK
        text encounter_id FK
        text doctor_id FK
        int version
        jsonb soap_json
        jsonb edit_history
        text signed_by
        timestamptz signed_at
        text content_hash
        uuid supersedes FK
    }
    medicine_stock {
        text medicine_id PK
        text clinic_id PK, FK
        text name
        text composition
        numeric_10_2 price_inr
        int qty
        timestamptz as_of
        text source
    }
    prescription {
        uuid rx_id PK
        uuid note_id FK
        text medicine_id
        text medicine_text
        text dose
        boolean substitute_ok
    }
    document {
        text doc_id PK
        text patient_id FK
        text type
        text uri
        text language
        timestamptz uploaded_at
        text ingest_status
        text phi_status
        boolean synthetic
        text title
        date report_date
        text error
    }
    doc_chunk {
        text chunk_id PK
        text doc_id FK
        text patient_id FK
        int page
        text section
        text text
        vector_1024 embedding
        text content_hash
        text_array flags
        tsvector tsv
    }
    audit_event {
        bigint event_id PK
        timestamptz at
        text actor_id
        text actor_role
        text action
        text resource_type
        text resource_id
        text patient_id
        text purpose
        text trace_id
        jsonb detail
    }
    frontdesk_handling {
        bigint event_id PK, FK
        text status
        text handled_by
        timestamptz handled_at
    }
    frontdesk_replay {
        text staff_id PK
        text idempotency_key PK
        text fingerprint
        jsonb result
    }
    w1_request {
        text user_id PK
        text idempotency_key PK
        text thread_id
        boolean running
        timestamptz created_at
    }
    trace_span {
        text trace_id
        text span_id PK
        text parent_span_id
        text workflow
        text name
        text kind
        timestamptz started_at
        double duration_ms
        text status
        text model
        text provider
        text served_by
        int tokens_in
        int tokens_out
        text error_type
    }
    specialty ||--o{ doctor : "specialty_id"
    clinic ||--o{ doctor : "clinic_id"
    patient ||--o{ consent : "patient_id"
    doctor ||--o{ slot : "doctor_id"
    clinic ||--o{ slot : "clinic_id"
    patient ||--o{ appointment : "patient_id"
    slot ||--o{ appointment : "slot_id"
    appointment |o--o| encounter : "appt_id unique nullable"
    patient ||--o{ encounter : "patient_id"
    doctor |o--o{ encounter : "doctor_id nullable"
    clinic |o--o{ encounter : "clinic_id nullable"
    encounter ||--o{ clinical_note : "encounter_id"
    doctor ||--o{ clinical_note : "doctor_id"
    clinical_note |o--o{ clinical_note : "supersedes nullable"
    clinic ||--o{ medicine_stock : "clinic_id"
    clinical_note ||--o{ prescription : "note_id"
    patient ||--o{ document : "patient_id"
    document ||--o{ doc_chunk : "COMPOSITE doc_id patient_id"
    audit_event ||--o| frontdesk_handling : "event_id PK FK"
```

## Database-enforced rules

- CHECKs restrict clinic city/kind/opening times; doctor gender, languages and practice years; consent scopes, expiry and distinct grantee; slot capacity/booked range; appointment status/booked_by; encounter time order; note version/SOAP keys/signer/supersedes; stock nonnegative price/quantity and source; document type/ingest/PHI status; chunk page; audit actor role/action; handling status.
- `book_slot` returns an existing appointment for `(patient_id,idempotency_key)`; otherwise it atomically increments available capacity and inserts. Appointment has that unique pair and a partial unique index on live `(patient_id,slot_id)`. `release_appointment` releases the seat transactionally.
- Only signed notes can be inserted. The hash trigger computes SHA-256 of SOAP JSON; update/delete are refused. `(encounter_id,version)` is unique. `signed_by = doctor_id` checks the note row, not authenticated identity or equality with the encounter doctor.
- `doc_chunk(doc_id,patient_id)` references `document(doc_id,patient_id)` with cascade delete; the parent pair is UNIQUE. This is one composite FK, not an independent chunk-to-patient FK.
- Audit update/delete are refused; `who_saw_record` projects patient-associated read events. Application code must still write the events. Privileged owners can bypass triggers; TRUNCATE is not covered.
- Migration 005 separates handling and replay state from immutable audit rows; 006 uses `(user_id,idempotency_key)` PK and unique `thread_id`. Neither table declares an FK from user/staff identifiers to patient.
- Migration 007 stores spans without prompt, question, symptom or patient text/IDs; audit `trace_id` is the correlation path. `purge_trace_spans()` removes rows older than 30 days and must be scheduled by operations.
- No FK exists for `prescription.medicine_id` (stock key is composite), audit patient/actor IDs, consent grantee, note `signed_by`, or handling staff. These are deliberately not drawn as relationships.

Sources: [001](../../infra/db/001_schema.sql), [002](../../infra/db/002_load_generated.sql), [003](../../infra/db/003_fix_forbid_change_errcode.sql), [004](../../infra/db/004_w2_records.sql), [005](../../infra/db/005_frontdesk.sql), [006](../../infra/db/006_w1_requests.sql), [007](../../infra/db/007_tracing.sql).

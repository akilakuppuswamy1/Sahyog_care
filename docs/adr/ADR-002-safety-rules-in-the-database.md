# ADR-002: Safety rules live in the database, not in prompts or application code alone

```
title: Booking capacity, consent, note immutability, chunk ownership and audit append-only are enforced by Postgres.
context: The pack says "a prompt can be argued with; a SQL filter cannot" (p.31). Four stakeholders are owed proofs that
  must hold even if a graph node, a tool or a future service has a bug: Meena (no double booking), Priya (no one
  else reads her reports), Dr. Rao (nothing persists unsigned, nothing changes after signing) and Kavita (who
  saw this record, in one query).
options:
  1. Rules in prompts and model instructions. Cheapest, but untestable and arguable. Rejected for anything safety-related.
  2. Rules in application code (service layer). Testable, but every new service or script must re-implement
     them, and a direct DB write bypasses them.
  3. Rules in the database: CHECK constraints, unique indexes, composite FKs, plpgsql functions and triggers,
     with application code as a second line.
decision: Option 3. Implemented in infra/db/001_schema.sql:
  - slot: CHECK (booked BETWEEN 0 AND capacity); book_slot() is idempotent on (patient_id, idempotency_key)
  - consent: a row with scope, purpose and expiry; consent_valid() is the only access check
  - clinical_note: signed_at NOT NULL, signed_by = doctor_id, sha256 content hash, UPDATE and DELETE raise
  - doc_chunk: patient_id with a composite FK to (document.doc_id, document.patient_id)
  - audit_event: append-only; the who_saw_record view answers E6-1
  Trigger to revisit: scale. If the slot row lock under book_slot() becomes a hot spot in the 1,400 req/h load
  test (Architect tier), move to an advisory-lock or reservation-queue design. The CHECK constraint stays.
consequences:
  - Proofs are tests against real Postgres: infra/db/tests/test_schema.py (11 tests, passing on the pod machine 2026-11-02).
  - Audit retention (1 year) cannot use DELETE. It needs monthly partitions that are detached and archived. Day-12 item.
  - Schema changes need migrations, as 003_fix_forbid_change_errcode.sql shows.
  - Lesson already paid for: the first trigger used SQLSTATE P0002 (no_data_found). Only the real-Postgres
    tests showed that drivers read a refused write as "no data".
versions_pinned: postgres 16 with pgvector (docker image pgvector/pgvector:pg16)
checked_on: 2026-11-02
status: accepted
```

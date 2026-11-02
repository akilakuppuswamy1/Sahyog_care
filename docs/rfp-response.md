# RFP addendum response

These answers express the client requirement. Status distinguishes today's Core implementation from planned Pro and Architect work; synthetic evidence is not production certification.

## 1. Can the assistant diagnose?

**Client answer:** No. It routes to a specialty and flags emergencies.
**Requirement:** Run the deterministic red-flag gate before matching or any model call; apply wording guardrails to patient answers.
**Implementation:** `services/agent/w1/nodes.py`, `services/agent/w1/redflags.py`, `services/agent/w1/guards.py`, and `services/agent/w2/nodes.py` implement routing, output checks and W2 verification/fallback.
**Proof:** `services/agent/tests/test_w1_graph.py::test_red_flag_ends_the_run_with_no_model_call_and_no_slot`; `services/records/tests/test_w2.py::test_diagnosis_wording_falls_back`; saved runs and limitations in `evals/report.md`.
**Status:** Built (Core) for deterministic gates and wording checks; regex checks do not prove all possible diagnostic wording is blocked.

## 2. Can a patient share records with family?

**Client answer:** Yes, through a caregiver role with expiring consent.
**Requirement:** Consent records identify patient, grantee, scopes, purpose and expiry; expired or out-of-scope consent must fail before reads.
**Implementation:** `infra/db/001_schema.sql` (`consent`, `has_consent`); `services/agent/w2/nodes.py` and `services/agent/w2/tools.py` check access before retrieval. Patient identity comes from authentication at the API boundary.
**Proof:** `infra/db/tests/test_schema.py::test_consent_valid_only_in_scope_and_before_expiry`; `services/records/tests/test_w2.py::test_caregiver_with_valid_consent_reads_the_wards_records`; `test_expired_missing_or_spoofed_consent_is_refused_before_any_read` in the same file.
**Status:** Partial (Core): schema and workflow checks built; demo authentication is not production identity or a patient consent-management UI.

## 3. Where does consultation audio go?

**Client answer:** Process it and delete it within 24 hours; retain transcript and note.
**Requirement:** In-region object storage, a 24-hour lifecycle rule and a deletion audit event, with verification of expiry.
**Implementation today:** `infra/db/001_schema.sql` provides encounter/transcript, signed-note and append-only audit structures. `docs/diagrams/w3-graph.md` describes the future scribe workflow. No consultation audio upload, object-store lifecycle or deletion worker is implemented.
**Proof:** `infra/db/tests/test_schema.py::test_audit_is_append_only_and_answers_who_saw` proves audit immutability only; no audio-retention test or run exists.
**Status:** Planned (Pro / W3); transcript/note schema exists, audio processing and deletion proof pending.

## 4. May identified data reach a public LLM?

**Client answer:** Only de-identified text may leave; identified text must remain in-region.
**Requirement:** Scrub and refuse unsafe egress before every public-model call; enforce in-region storage and processing for identifiers.
**Implementation:** `services/agent/w1/guards.py` and `services/agent/llm.py` provide regex scrub/refusal at egress; `services/records/parse.py` removes fixture identity blocks before embedding/retrieval. Deployment intent is documented in `infra/deployment-adr.md`.
**Proof:** `services/records/tests/test_w2.py::test_identifiers_never_enter_a_chunk`; guard tests under `services/agent/tests/`. These are bounded tests, not full de-identification or residency verification.
**Status:** Partial (Core); complete de-identification and verified regional hosting are Architect deployment work. Current controls cannot certify the client requirement for arbitrary identified text.

## 5. Where does stock data come from?

**Client answer:** Nightly CSV now, an API in approximately six months.
**Requirement:** One stock interface with CSV and API adapters, freshness indicators and an explicit unknown state for stale stock.
**Implementation today:** `data/transforms/medicines.py` prepares formulary data; `infra/db/001_schema.sql` defines stock-related data. `docs/assumptions.md` A02 records the nightly-feed assumption. Formulary data is not live inventory. The stock interface and two adapters are not built.
**Proof:** Schema/transform tests provide data-layer evidence only; no stock-adapter or freshness proof exists.
**Status:** Planned (Pro); API adapter follows client availability, approximately six months after the CSV integration.

## 6. Who approves AI-drafted notes?

**Client answer:** The consulting doctor, every time.
**Requirement:** Interrupt the graph for doctor review before persistence; unsigned notes remain drafts. Signed notes are immutable and corrections create new versions.
**Implementation:** `infra/db/001_schema.sql` requires `signed_by`, `signed_at`, signer/doctor equality, content hash and immutability. Drafts are intended to live in checkpoint state. `docs/diagrams/w3-graph.md` designs the review interrupt; there is no executable W3 graph yet. Signer equality alone is not doctor authentication.
**Proof:** `infra/db/tests/test_schema.py::test_unsigned_note_cannot_be_stored`, `test_signed_note_is_immutable_and_hashed`, and `test_only_treating_doctor_can_sign`.
**Status:** Partial: signed-note schema built (Core); authenticated doctor review, draft state and graph interrupt planned (Pro / W3).

## 7. What volume must it support?

**Client answer:** 9,000 appointments/day and 1,400 requests/hour at peak.
**Requirement:** Load-test that workload and latency/error budgets; async ingest and stateless APIs behind a balancer with durable checkpoint state.
**Implementation today:** `services/api/main.py`, `services/api/deps.py`, `infra/docker-compose.yml`, `services/agent/w1/cli.py` and `docs/api-controls.md`. Core runs one API worker, in-process upload tasks and local rate-limit buckets; W1 uses Postgres checkpoints, W2 has no durable checkpointer. `tools/load_book.py` is a manual scripted-model rate-limit probe, not a capacity result.
**Proof:** `services/api/tests/test_controls.py::test_twenty_successful_bookings_then_limit` proves rate limiting, not throughput; `services/agent/tests/test_w1_graph.py::test_resume_after_restart_uses_the_checkpoint` proves recompiled-graph resume with a shared saver, not a real crash drill. No target-volume load test has run.
**Status:** Partial (Core service); queue, distributed controls, balancer and target-volume load test planned (Architect). No claim of meeting the volume target today.

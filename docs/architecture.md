# Architecture pack — version 2

Status: 2026-11-02. W1/W2 and Core API are built; W3 is a Pro design. India residency is a deployment target, not a claim about this local demo. This supersedes v1's prospective diagrams.

## Views and decisions

- [Context — C4 level 1](diagrams/context.md)
- [Containers — C4 level 2, built/planned](diagrams/container.md)
- [Entity model — migrations 001–006](diagrams/entity-model.md)
- [W1 compiled graph](diagrams/w1-graph.md)
- [W2 compiled graph](diagrams/w2-graph.md)
- [W3 scribe DESIGN — Pro, not built](diagrams/w3-graph.md)
- [W1 booking sequence](diagrams/seq-w1-booking.md)
- [W2 cited answer sequence](diagrams/seq-w2-answer.md)
- [ADR-001 — hybrid deployment](adr/ADR-001-hybrid-deployment.md)
- [ADR-002 — database safety rules](adr/ADR-002-safety-rules-in-the-database.md)
- [ADR-003 — W1 graph shape](adr/ADR-003-w1-graph-shape.md)
- [D6 deployment ADR](../infra/deployment-adr.md)
- [D7 threat model and access review](d7-threat-model.md)

Regenerate W1/W2 with `python tools/export_graphs.py` (or `--output-dir PATH`). The exporter compiles with ScriptedLLM, InMemoryStore.demo(), InMemoryRecordsTools, HashEmbedder and InMemorySaver, then calls `get_graph().draw_mermaid()`. It does not invoke either graph or construct production dependencies. Regression: `services/agent/tests/test_export_graphs.py::test_export_compiled_graphs`.

## Corrected from the pack

Pack references below are the task's supplied comparisons and the repo's existing ADR/v1 notes. The deck itself is not in this workspace; its exact artwork and page text have not been independently checked. Every statement of built behaviour below was checked against implementation.

| Pack/opening view | Corrected design and reason | Implementation evidence |
| --- | --- | --- |
| One apparent red-flag stage (p.25) | Rules-only red-flag gate precedes any model. A rules hit ends in handoff. Model net can only add flags; it cannot clear a rules flag. | `services/agent/w1/graph.py::build_w1`, `nodes.py::red_flag_rules`, `redflags.py::merge` |
| Sequential net then specialty match | `model_checks` runs net and match in a two-worker ThreadPoolExecutor for latency. Any net flag wins over match. The exported graph correctly has one model_checks node, not two sequential nodes. | `services/agent/w1/nodes.py::model_checks` |
| Language detection drawn as a small model | Built language detection is a deterministic heuristic. | `services/agent/w1/matcher.py::detect_language` |
| All W1 questions go to matching | Records-only questions without symptom terms redirect to My records before a model call; rules still take priority. | `services/agent/w1/nodes.py::records_question`, `red_flag_rules`, `redirect_records` |
| Any keyword hit treated as relevant W2 context | Grade uses vector score >= 0.45 OR topic-word coverage > 0.5; widens k=6 to 12 once, then not_found. This avoids irrelevant shared header words admitting an answer. Grade is an edge function. | `services/agent/w2/nodes.py::grade`, `services/records/retrieve.py::coverage` |
| Model answer assumed grounded | Deterministic verifier resolves citations to retrieved subject chunks, checks numeric membership and diagnosis/dose wording; usable citations allow verbatim-line fallback. This is heuristic verification, not semantic proof. | `services/agent/w2/nodes.py::answer`, `verify`; `services/agent/w1/guards.py::DIAGNOSIS` |
| Triage retry starts another run | After the 2026-11-02 incident identified in this task, migration 006 reserves `(user,key)` before model/checkpoint work. Same key returns the same thread; running returns 202; completed reads current state. Test proves concurrent replay and restart. Incident narrative beyond this fix is not inferred from code. | `services/api/deps.py::W1Requests`, `services/api/main.py::triage`, `infra/db/006_w1_requests.sql`; `services/api/tests/test_api_postgres.py::test_postgres_pick_slot_resume_and_replay` |
| 1,400-doctor split (p.19) versus 500 generated doctors (p.20) | Directory target is 500 doctors. 1,400 is not the doctor population. | `data/synthetic/generate_doctors.py::TARGET_DOCTORS`; `data/transforms/tests/test_generated.py` |
| LLM-generated doctor directory (p.20) | Seeded code produces the directory for reproducibility, explicit distributions and auditability. Reports also use seeded templates. | `data/synthetic/generate_doctors.py`, `generate_reports.py`, `report_cohort.py` |
| Remote/ambiguous embedding boundary | bge-m3 runs locally; no report text is sent to an embedding vendor. This supports the residency boundary; it does not establish hosting residency. | `services/records/embed.py::BgeM3Embedder`, `services/api/deps.py::build_postgres_deps` |
| DocChunk lacks patient identity (p.18) | Every chunk has patient_id and a composite document FK; all three retrieval SQL queries filter patient_id. A model cannot choose the retrieval subject. | `infra/db/001_schema.sql`; `services/records/retrieve.py::PostgresRetriever.search` |
| Draft and signed notes share persistence (p.18) | Built clinical_note table requires signature fields, computes a content hash and rejects update/delete; W3 draft checkpoints/review queue remain planned Pro. Schema is not a built scribe workflow. | `infra/db/001_schema.sql`; [W3 design](diagrams/w3-graph.md) |
| Stock status checked before doctor edits (p.27–28) | Proposed W3 loops edits through grounding and stock checks before signing; final medicine determines status. | Design only; no W3 implementation claimed |
| Prospective API gateway, Redis and Presidio drawn as deployed | Core is one FastAPI process with a lock, demo bearer auth, in-process patient/IP buckets, regex egress and local upload files. OIDC/OTP and Presidio are Pro targets; distributed limits are Architect. | `services/api/{auth,ratelimit,deps,main}.py`, `services/agent/llm.py`; [D6](../infra/deployment-adr.md) |
| SMS relay treated as delivery | notify writes an audit event named sms_outbox with a template ID. There is no persistent outbox table or carrier worker. Delivery remains planned Pro. | `services/agent/w1/nodes.py::notify`, migrations 001–006 |
| 14 application tables everywhere | Migrations 005 and 006 add frontdesk_handling, frontdesk_replay and w1_request: 17 persistent tables, plus runtime-managed checkpoint tables. | [Entity model](diagrams/entity-model.md) |

## Operational limits and evidence

The production dependency factory gives W1 a PostgresSaver; W2 has no checkpointer. API ownership checks and consent checks precede reads/resumes, but demo bearer subjects are forgeable. Front desk routes distinguish staff from patients only through that demo mechanism. Least-privilege database roles and stronger identity are not implemented.

Booking capacity and appointment idempotency are database enforced. Triage replay has a persistent mapping; resume and upload replay caches are process-local. A crash before finishing a triage reservation can leave it running indefinitely. Replay does not fingerprint triage text. Explicit audit writes cover returned documents in every retrieval pass, not all internal SQL candidates or arbitrary direct database reads.

Regex scrubbing and refuse-to-send are built for known identifier patterns; names and quasi-identifiers can survive. The scrub token map is ephemeral and local. Presidio, re-identification, zero-retention vendor terms verification, outbound payload hash logging, the 200-payload egress replay, storage encryption, checkpoint purge and India hosting are not asserted as built. See D7 for delivery targets and residual risk. No claim of complete de-identification, cryptographic signatures or universal read auditing is made.

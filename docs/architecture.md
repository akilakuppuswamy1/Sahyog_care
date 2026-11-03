# Architecture pack — version 2

Status: Pro (`pro-complete`), with the unmet quality and deployment targets in the [evaluation report](../evals/report.md). W1/W2, Core API, Postgres tracing, durable queued ingest with a separate worker and owner-only job status, the W3 scribe graph, doctor API/console and persistence, and the MCP scheduling/stock tool server are built; the W3 timeout job is not scheduled in the demo. The two-replica Compose profile, Postgres-backed distributed controls and MCP affinity are configured; target-volume p95 results remain unmeasured until a saved load run passes. MCP still uses demo identity. India residency is a deployment target, not a claim about this local demo. This supersedes v1's prospective diagrams.

## Views and decisions

- [Context — C4 level 1](diagrams/context.md)
- [Containers — C4 level 2, built/planned](diagrams/container.md)
- [Entity model — migrations 001–009](diagrams/entity-model.md)
- [W1 compiled graph](diagrams/w1-graph.md)
- [W2 compiled graph](diagrams/w2-graph.md)
- [W3 scribe graph — built](diagrams/w3-graph.md)
- [W1 booking sequence](diagrams/seq-w1-booking.md)
- [MCP booking sequence](diagrams/seq-mcp-booking.md)
- [W2 cited answer sequence](diagrams/seq-w2-answer.md)
- [ADR-001 — hybrid deployment](adr/ADR-001-hybrid-deployment.md)
- [ADR-002 — database safety rules](adr/ADR-002-safety-rules-in-the-database.md)
- [ADR-003 — W1 graph shape](adr/ADR-003-w1-graph-shape.md)
- [ADR-005 — in-region tracing](adr/ADR-005-tracing.md)
- [ADR-006 — MCP tool server](adr/ADR-006-mcp-tool-server.md)
- [ADR-007 — scaling and load verification](adr/ADR-007-scaling-and-load.md)
- [Scale deployment diagram](diagrams/deployment-scale.md)
- [D6 deployment ADR](../infra/deployment-adr.md)
- [D7 threat model and access review](d7-threat-model.md)

Regenerate W1/W2/W3 with `python tools/export_graphs.py` (or `--output-dir PATH`). The exporter compiles with scripted/in-memory adapters and `InMemorySaver`, then calls `get_graph().draw_mermaid()`. It does not invoke a graph or construct production dependencies. Regression: `services/agent/tests/test_export_graphs.py::test_export_compiled_graphs`.

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
| Triage retry starts another run | Migration 006 reserves `(user,key)` before model/checkpoint work; 014 binds the claim to the request fingerprint. Same payload returns the same thread; a changed payload returns 409. Resume replay and upload request results are also shared in Postgres. | `services/api/deps.py::W1Requests`, `services/api/deps.py::ReplayRequests`, `services/api/main.py`, `infra/db/014_horizontal_api_state.sql`; `services/api/tests/test_api_postgres.py::test_postgres_pick_slot_resume_and_replay` |
| 1,400-doctor split (p.19) versus 500 generated doctors (p.20) | Directory target is 500 doctors. 1,400 is not the doctor population. | `data/synthetic/generate_doctors.py::TARGET_DOCTORS`; `data/transforms/tests/test_generated.py` |
| LLM-generated doctor directory (p.20) | Seeded code produces the directory for reproducibility, explicit distributions and auditability. Reports also use seeded templates. | `data/synthetic/generate_doctors.py`, `generate_reports.py`, `report_cohort.py` |
| Remote/ambiguous embedding boundary | bge-m3 runs locally; no report text is sent to an embedding vendor. This supports the residency boundary; it does not establish hosting residency. | `services/records/embed.py::BgeM3Embedder`, `services/api/deps.py::build_postgres_deps` |
| DocChunk lacks patient identity (p.18) | Every chunk has patient_id and a composite document FK; all three retrieval SQL queries filter patient_id. A model cannot choose the retrieval subject. | `infra/db/001_schema.sql`; `services/records/retrieve.py::PostgresRetriever.search` |
| Draft and signed notes share persistence (p.18) | W3 stores editable `note_draft` and `note_edit` rows; only approval inserts a signed, hashed, immutable clinical note. The database signer trigger and authenticated API bind the note to the encounter doctor. | `infra/db/001_schema.sql`, `infra/db/008_w3_drafts.sql`, `infra/db/010_w3_api.sql`; `services/agent/w3/store.py`, `services/api/main.py` |
| Stock status checked before doctor edits (p.27–28) | CSV and API stock adapters share an interface. Edits loop through grounding and stock; alternatives are suggestion-only and never substituted. The API adapter is an explicit unconfigured stub. | `services/agent/w3/stock.py`, `services/agent/w3/graph.py` |
| Prospective API gateway, Redis and Presidio drawn as deployed | The app profile remains a local single API process; the `scale` profile configures two API replicas behind Nginx. Rate buckets and W1-resume/upload replay claims are shared in Postgres, not Redis. MCP alone uses source-IP affinity because Streamable HTTP session state remains process-local. Presidio egress and local upload storage are built; OIDC/OTP remain unbuilt. | `services/api/{auth,ratelimit,deps,main}.py`, `infra/docker-compose.yml`, `infra/db/014_horizontal_api_state.sql`; [ADR-007](adr/ADR-007-scaling-and-load.md) |
| SMS relay treated as delivery | W1 and W3 write audit events for notification intent. There is no persistent outbox table or carrier worker. Delivery remains planned Pro. | `services/agent/w1/nodes.py::notify`, `services/agent/w3/store.py::notify_signed` |
| Observability assumed to require Langfuse Cloud | Migration 007 stores allowlisted spans in Postgres in-region; a `SpanExporter` supports a later self-hosted Langfuse replacement. Cloud was rejected for residency. Traces exclude prompts, questions, symptom text and patient IDs; the audit log maps trace IDs to patients. | [ADR-005](adr/ADR-005-tracing.md), `services/common/tracing.py`, `infra/db/007_tracing.sql` |
| Application table inventory | Migrations 005–007 add frontdesk_handling, frontdesk_replay, w1_request and trace_span; W3 migrations 008–010 add note_draft, note_edit and w3_request; 014 extends W1 request fingerprints and adds API rate/replay tables; 015 allows rate-limit decisions in the audit-action constraint. | [Entity model](diagrams/entity-model.md) |

## Operational limits and evidence

The production dependency factory gives W1 and W3 a PostgresSaver; W2 has no checkpointer. W3 API ownership checks precede reads/resumes, but demo bearer subjects are forgeable. Front desk and doctor routes distinguish roles only through that demo mechanism. Least-privilege database roles and stronger identity are not implemented.

Booking capacity and appointment idempotency are database enforced. Triage claims, rate-limit buckets, W1 resume replay, and upload request replay are shared in Postgres. Resume/upload claims expire after a bounded lease; triage claims still require operator recovery if a process dies before completion. Triage replay fingerprints the request body and rejects changed payloads for a reused key. Explicit audit writes cover returned documents in every retrieval pass, not all internal SQL candidates or arbitrary direct database reads.

Migration 012 adds `ingest_job` (22 application tables in total). Upload content hashes
are unique per authenticated owner, so identical PDF submissions reuse the durable
document/job across API restarts; Idempotency-Key replay responses are also stored in
Postgres for cross-replica retries. Both API replicas and the worker mount the same
uploads volume. The separate worker claims with `FOR UPDATE SKIP LOCKED`, fences
live work with session locks, recovers disconnected attempts, and stops after three
attempts. `/records/api/jobs/{job_id}` filters by owner; Ops queue depth and oldest
queued age come from `ingest_job`. See the container view and README worker steps.

The worker claims batches of up to `INGEST_BATCH_SIZE` (default 8) and uses the
separate records LangGraph with `Send` and `INGEST_MAX_CONCURRENCY` (default 4).
Documents parse, strip identity blocks, chunk, apply injection/PHI flags and embed
independently; a join precedes per-job completion/retry updates. Each batch has one
parent trace and one child span per document. The W2 answer graph is unchanged. See
the [ingest fan-out diagram](diagrams/ingest-fanout.md).

Tracing is built with Postgres as the only span store. The `/ops` screen and JSON endpoints require
the demo `ops` role; each populated tile names its source. `purge_trace_spans()` deletes spans older
than 30 days, but no scheduler is installed. The cost worksheet is generated from saved eval JSON;
its per-consultation scenario is explicitly illustrative because the current runs do not link W1, W2
and W3 into real consultation counts. Scribe review-time and edit-distance ops tiles appear only
after a signed note supplies source timestamps and an initial draft.

Presidio/spaCy and custom Indian recognisers, typed tokens, local recursive reply restoration and the regex refuse-to-send guard are built in the shared egress adapter, including every hedge. Token maps stay in-process and outside spans/checkpoints; model spans store numeric deid_ms only (migration 011). Offline replay [egress-20261103-051910](../evals/runs/egress-20261103-051910.json) covers 200 synthetic payloads with 0 hits and p95 14.296 ms. Statistical name recognition and contextual quasi-identifiers retain residual risk. Vendor retention verification, storage encryption, checkpoint purge and India hosting remain deployment work; see D7.

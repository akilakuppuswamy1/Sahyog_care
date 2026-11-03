# Core API controls

Authenticated W1 triage and resume writes require an `Idempotency-Key` header
of 8–128 characters. Generate a fresh key for each action and reuse that key
for retries. W1 passes the resume key to the booking store; the database's
unique `(patient_id, idempotency_key)` constraint deduplicates appointments.
CLI callers retain the trace/slot fallback. The demo submits triage once, disables its button while working, and polls the owner-only `GET /w1/{thread_id}` every two seconds for up to 30 seconds when running or recovering an error with a known thread ID. Transport failures before any thread ID reaches the browser cannot be polled; the page reports the error without resubmitting.

The API uses Postgres-backed token buckets for both authenticated patient and
connection address, shared by API replicas and MCP. SHA-256 bucket identifiers
avoid storing patient IDs or addresses in the limiter table; Postgres advisory
locks make refill and consume atomic across processes. Reads (including triage and clarification)
default to `RL_READ_PER_MIN=30`; pick-slot resumes use the separate
`RL_BOOK_PER_HOUR=20` bucket. Invalid slot choices consume booking tokens too.
429 responses include `Retry-After` and produce a `rate_limited` audit event.
Clients sharing an address share its bucket. Initial W1 triage requests are
fingerprinted and reserved durably in `w1_request` (migration 006, extended by
014); in-flight replays return 202 running and completed requests read the
shared checkpoint. W1 resume responses and upload idempotency are stored in
`api_replay_request` with a 15-minute claim lease. Appointment and content-hash
deduplication remain database-enforced. In-memory request stores and buckets are
only used by explicitly injected test dependencies.

Each request produces JSON metadata with UTC timestamp, method, route-template
path, status, latency, hashed user, trace/thread IDs, and workflow route. Body
text is excluded. W1/W2 responses include `X-Trace-Id`, including errors.

## Manual booking-limit probe

`tools/load_book.py` is manual only. Point it at a running API configured with
ScriptedLLM, a synthetic patient, and at least 21 available matching slots.
Do not use an API backed by OpenRouter. Start with empty rate-limit buckets.

```sh
python tools/load_book.py --base-url http://localhost:8000 --patient PT-LOAD
```

The probe makes 1,000 booking attempts with fresh keys. After the first 429 it
keeps submitting the paused booking, so further triage reads do not obscure
the booking limit. With defaults and sufficient capacity, expect about 20
successes and 980 responses with status 429. The probe fails explicitly on
setup failures, such as exhausted appointment capacity or a non-pick-slot
interrupt.

## Horizontal load test

The Compose `scale` profile starts two API replicas and Nginx on port 8080.
Normal HTTP traffic is round-robin; `/mcp/` uses `ip_hash` because Streamable HTTP
session state is process-local. A changed client IP or lost replica requires a
new MCP initialization; normal REST requests do not need affinity.

`python tools/load_test.py` schedules 1,400 HTTP requests/hour for 15 minutes by
default and supports `--burst` (5x for the first two minutes). It uses synthetic
subjects loaded from Postgres, fresh idempotency keys, demo tokens and the
load-test model stub. Only LLM calls are replaced; safety rules, de-identification,
retrieval, persistence, tracing and upload enqueue remain active. Run results are
saved as `evals/runs/load-<timestamp>.json/.md`; targets are W1 p95 <4 s, W2
p95 <8 s and W3 p95 <20 s. The report generator and `/ops` read the latest saved
result. See [ADR-007](adr/ADR-007-scaling-and-load.md).

## Front desk and uploads

Demo staff tokens `Bearer demo-staff-<ID>` have role `front_desk` only with
`SAHYOG_DEMO_AUTH=1`; ops tokens `Bearer demo-ops-<ID>` have role `ops` only
with that setting. `GET /frontdesk` and `GET /ops` return static HTML shells
without authentication and contain no patient data. Each page stores its demo
token in session storage and sends it as a bearer token on every API request.
All front-desk and ops data endpoints require the matching role: requests with
no token receive 401, and patient or other-role tokens receive 403.

| Route | Role | Control |
|---|---|---|
| GET /frontdesk | public | Static HTML shell only |
| GET /frontdesk/api/escalations?status=open | front_desk | Audited queue from W1 audit hand-offs |
| POST /frontdesk/api/escalations/{id}/handled | front_desk | Idempotency-Key required; handling stored separately from immutable audit |
| GET /frontdesk/api/bookings?booked_by=assistant | front_desk | Audited appointment, slot, doctor, clinic, ISO and IST time |
| POST /frontdesk/api/bookings/{appt_id}/cancel | front_desk | Idempotency-Key required; old row overridden, capacity released |
| POST /frontdesk/api/bookings/{appt_id}/move | front_desk | Body `{slot_id}`; required Idempotency-Key; atomic override and book_slot with front_desk attribution |
| GET /ops | public | Static HTML shell only |
| GET /ops/api/summary | ops | Sourced operational metrics |
| GET /ops/api/traces/{trace_id} | ops | Trace tree |
| POST /records/upload | patient | Required Idempotency-Key; current user only, multipart PDF magic and 10 MB limit; 202 queued |
| GET /records/documents | patient | Own documents only; status/error and ISO plus IST upload time |
| DELETE /records/documents/{doc_id} | patient | Erases only an owned patient upload; file deletion is confined to the upload root; hospital reports and signed W3 notes return 409 |
| POST /records/consents | patient | Grants caregiver scope and expiry; patient identity comes from the bearer token |
| DELETE /records/consents/{caregiver_id} | patient | Withdraws active caregiver consent and writes an audit event |
| POST /w2/ask | patient, caregiver | Patient identity is taken from the bearer token; a demo caregiver token uses `caregiver-{caregiver_id}-for-{patient_id}` and is limited to W2; consent is checked on each request |

Uploads use the durable ingest queue and worker, with owner-bound job status.
Erasure serializes against an active ingest job and removes the upload, document,
chunks/embeddings, document PHI token map, and job link in the same audited
operation. Only files resolved inside the configured upload root may be removed.

## Incident 2026-11-02

The browser showed 409 “Thread is not paused” on a slot click. The reported
uvicorn JSON logs showed an 11.6 s W1 triage, a repeated POST with the same
Idempotency-Key, and a 200 replay with a null thread_id. The demo retry lost
the active thread; the original Postgres test never retried. Triage now
reserves a persistent (user, key) -> thread_id before graph invocation,
returns 202 with that ID during execution, and reads the live checkpoint on
replay; owner-only GET supports polling. The demo submits once, blocks double
clicks, preserves non-null thread IDs, and polls known threads. Slow
ScriptedLLM concurrent-request regressions in test_api.py and
test_api_postgres.py cover running replay, pending-interrupt replay, ownership,
and successful booking resume.

# Core API controls

Authenticated W1 triage and resume writes require an `Idempotency-Key` header
of 8â€“128 characters. Generate a fresh key for each action and reuse that key
for retries. W1 passes the resume key to the booking store; the database's
unique `(patient_id, idempotency_key)` constraint deduplicates appointments.
CLI callers retain the trace/slot fallback. The demo submits triage once, disables its button while working, and polls the owner-only `GET /w1/{thread_id}` every two seconds for up to 30 seconds when running or recovering an error with a known thread ID. Transport failures before any thread ID reaches the browser cannot be polled; the page reports the error without resubmitting.

The single-replica API uses thread-safe token buckets for both authenticated
patient and connection address. Reads (including triage and clarification)
default to `RL_READ_PER_MIN=30`; pick-slot resumes use the separate
`RL_BOOK_PER_HOUR=20` bucket. Invalid slot choices consume booking tokens too.
429 responses include `Retry-After` and produce a `rate_limited` audit event.
Clients sharing an address share its bucket. Architect deployments must move
these limits to the gateway/Redis. Triage keys map durably to thread IDs in `w1_request` (migration 006); in-flight replays return 202 running and completed replays read the current checkpoint. Other response replay caching is process-local;
completed booking resumes also recover from checkpoint state, and appointment
deduplication remains in the store.

Each request produces JSON metadata with UTC timestamp, method, route-template
path, status, latency, hashed user, trace/thread IDs, and workflow route. Body
text is excluded. W1/W2 responses include `X-Trace-Id`, including errors.

## Manual load probe

`tools/load_book.py` is manual only. Point it at a running API configured with
ScriptedLLM, a synthetic patient, and at least 21 available matching slots.
Do not use an API backed by OpenRouter. Start with empty rate-limit buckets.

```sh
python tools/load_book.py --base-url http://localhost:8000 --patient PT-LOAD
```

The probe makes 1,000 booking attempts with fresh keys. After the first 429 it
keeps submitting the paused booking, so further triage reads do not obscure the
booking limit. With defaults and sufficient capacity, expect about 20 successes
and 980 responses with status 429. The probe fails explicitly on setup failures,
such as exhausted appointment capacity or a non-pick-slot interrupt.


## Front desk and uploads

Demo staff tokens `Bearer demo-staff-<STAFF_ID>` have role `front_desk` only with `SAHYOG_DEMO_AUTH=1`. Patient and staff API routes reject the other role with 403. `GET /frontdesk` serves the demo shell with staff authentication. Open `/?view=frontdesk` to load the public shell and enter the staff ID; all data requests require staff authentication.

| Route | Role | Control |
|---|---|---|
| GET /frontdesk | front_desk | Audited staff demo page |
| GET /frontdesk/escalations?status=open | front_desk | Audited queue from W1 audit hand-offs |
| POST /frontdesk/escalations/{id}/handled | front_desk | Idempotency-Key required; handling stored separately from immutable audit |
| GET /frontdesk/bookings?booked_by=assistant | front_desk | Audited appointment, slot, doctor, clinic, ISO and IST time |
| POST /frontdesk/bookings/{appt_id}/cancel | front_desk | Idempotency-Key required; old row overridden, capacity released |
| POST /frontdesk/bookings/{appt_id}/move | front_desk | Body `{slot_id}`; required Idempotency-Key; atomic override and book_slot with front_desk attribution |
| POST /records/upload | patient | Required Idempotency-Key; current user only, multipart PDF magic and 10 MB limit; 202 queued |
| GET /records/documents | patient | Own documents only; status/error and ISO plus IST upload time |

Core ingestion runs in a FastAPI in-process background task with the startup-loaded embedder. Pro moves it to a queue with a job id. Restarting Core can interrupt a pending upload. Uploads use the fixture ingestion guards, including identity stripping and injection flags.

## Incident 2026-11-02

The browser showed 409 “Thread is not paused” on a slot click. The reported uvicorn JSON logs showed an 11.6 s W1 triage, a repeated POST with the same Idempotency-Key, and a 200 replay with a null thread_id. The demo retry lost the active thread; the original Postgres test never retried. Triage now reserves a persistent (user, key) -> thread_id before graph invocation, returns 202 with that ID during execution, and reads the live checkpoint on replay; owner-only GET supports polling. The demo submits once, blocks double clicks, preserves non-null thread IDs, and polls known threads. Slow ScriptedLLM concurrent-request regressions in test_api.py and test_api_postgres.py cover running replay, pending-interrupt replay, ownership, and successful booking resume.

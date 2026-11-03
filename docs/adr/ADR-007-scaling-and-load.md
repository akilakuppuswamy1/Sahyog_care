# ADR-007: horizontal API scaling and load verification

Status: accepted

## Context

The Architect target is 1,400 HTTP requests/hour and workflow p95 below 4 s for W1
triage, 8 s for W2 record answers and 20 s for W3 note drafts. The API must run
behind a balancer while any replica can resume a W1 thread. Patient safety,
de-identification, record retrieval, persistence and tracing must not be bypassed
to make a capacity test pass.

## Decision

- Keep W1/W3 checkpoints, booking/idempotency data, trace spans, rate buckets and
  API replay results in Postgres. Migration 014 adds shared hashed rate buckets and
  leased replay claims for W1 resumes and uploads; migration 006 already stores
  initial W1 thread reservations. Triage idempotency is bound to the request body.
- Use a two-API scale profile with Nginx. Ordinary HTTP routes use round-robin
  upstream selection. `/mcp/` uses `ip_hash` affinity because the Streamable HTTP
  SDK holds transport session state in the API process. No session state is moved
  into the business database.
- Keep MCP affinity limited to that path so a single load-test client can exercise
  both API replicas on ordinary routes. A client IP change or a replica restart
  can lose an MCP transport session; clients must initialize a new session. This
  is an explicit limitation of demo routing, not a production identity guarantee.
- Use Postgres-backed patient/IP buckets in the mounted and standalone MCP/API
  paths. Store only SHA-256 bucket identifiers, refill/consume atomically under
  Postgres advisory locks, and preserve the existing read/book limits.
- Allow process-local state only when it is not workflow state: model circuit
  breakers are per replica availability guards; provider-warning suppression and
  bounded model-call semaphores are logging/request-concurrency controls. No
  process-local cache is authoritative for a booking, replay result, checkpoint,
  rate-limit decision or upload job.
- `LLM_STUB=1` is an explicit local/demo load-test mode and is rejected unless
  `SAHYOG_DEMO_AUTH=1`. It replaces only model responses. Presidio egress and
  restoration, W1 red-flag rules, W2 retrieval/verification, database operations,
  upload queuing and Postgres tracing remain active.
- Default stub delay is selected deterministically per trace and model purpose:
  95% of calls use the saved workflow p50 and 5% use p95. The latest saved runs
  used are W1 `w1-20261103-071432-graph.json` (60/2,870 ms), W2
  `w2-20261103-104837.json` (1,000/3,560 ms) and W3
  `w3-20261103-114035.json` (2,413/6,583 ms). `LLM_STUB_LATENCY_MS` overrides
  this with a fixed non-negative integer. The load report records the selected
  provenance.

## Alternatives considered

- Redis would provide distributed leases and token buckets, but adds another
  stateful service and operational dependency. Postgres is already required for
  workflow state and supports the needed atomic operations at the current target.
- Sticky sessions for ordinary HTTP would not make replay claims or rate limits
  correct after reconnects, retries, or replica changes. Affinity is retained
  only for MCP's process-local transport sessions.
- A single API replica is simpler, but has no horizontal capacity or replica
  failover and cannot verify the requested two-replica behavior.

## Verification

`tools/load_test.py` uses the pinned `httpx==0.28.1` dependency and synthetic
database subjects. Its default run schedules the mixed workload at 1,400
requests/hour for 15 minutes with a 3x peak during the first two minutes before
returning to baseline; `--no-burst` disables the peak. It records per-endpoint
and workflow p95 target status, RPS, error/429/degraded rates and observed replica
IDs in paired JSON/Markdown files.
The evaluation report generator and `/ops` read the latest saved load result.

The cross-replica Postgres test starts W1 on one app instance, resumes and books
on a second instance, then retries the same action and asserts one appointment.
The load test itself must be run against the `scale` profile before claiming that
the target p95s pass; configuration and code do not constitute a measured result.

## Consequences

- Any API replica can read shared checkpoints, reserve/resume a thread, and
  recover completed booking/upload replays from Postgres.
- Sticky routing is confined to MCP transport sessions; ordinary HTTP requests
  can reach either API instance.
- Postgres is an availability dependency for distributed limits and replay claims.
  Failure is explicit rather than silently reverting production requests to
  per-process controls.
- Revisit this decision if Postgres contention or availability limits the target
  throughput, if MCP transport sessions must survive source-IP/replica changes,
  or if measured load exceeds the documented 1,400 requests/hour target.
- The stub is not evidence of live-model latency, answer quality or clinical
  suitability. Load-test reports state the stub mode and saved-run provenance.
- Demo authentication, local compose credentials, single-node Postgres, shared
  local upload storage and unencrypted volumes remain non-production controls.

## References

- [Scale deployment diagram](../diagrams/deployment-scale.md)
- [Core API controls](../api-controls.md)
- [MCP guide](../mcp.md)
- [Evaluation report](../../evals/report.md)

# ADR-005: in-region tracing

Status: accepted

## Context

Pro needs request, graph-node, tool and model timings, plus a trace link from operational views
back to the existing audit events. Trace storage must not create another path for prompt or patient
content to leave the region.

## Decision

- Store the allowlisted span fields in Postgres using migration 007. Postgres is the in-region store;
  no new service is introduced.
- Spans contain a trace ID but no prompt, question, symptom text or patient ID. The existing
  append-only audit log maps trace IDs to patients.
- Keep a `SpanExporter` interface. A self-hosted, in-region Langfuse exporter may replace the
  Postgres exporter after review.
- Reject Langfuse Cloud because it would move traces outside the required residency boundary.
- Retain spans for 30 days. `purge_trace_spans()` removes rows older than 30 days; operators must
  schedule it (the repository does not install a database scheduler).
- Exporter failures are logged and do not fail an application request.

## Consequences

- Ops metrics and trace trees use Postgres as their source; the existing database remains the only
  trace service.
- Database backups and hosting must remain in the approved region for the residency goal to hold.
- A trace ID is correlatable to a patient through audit access and must be treated as sensitive
  metadata even though spans contain no patient ID or clinical text.

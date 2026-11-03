# ADR-006: MCP tool server for scheduling and stock

Status: accepted

## Context

The Architect roadmap (p.52) calls for any approved hospital agent to book or check stock
under the same audit. This is a shared integration surface, not a private W1 function.
The existing Core API already owns demo authentication, rate limiting, scheduling
transactions, W3 stock adapters, audit events and trace export.

## Options

1. **REST only:** keep scheduling and stock as HTTP endpoints and require every agent
   host to build and maintain a custom adapter. This reuses the current API but does not
   provide a standard agent-facing tool catalog or typed input schemas.
2. **MCP alongside REST:** publish a typed tool catalog over Streamable HTTP while
   delegating operations to the existing store and stock adapter. Keep REST unchanged
   for current clients.

## Decision

Choose MCP alongside REST. Mount the Streamable HTTP server at `/mcp/` in FastAPI and
retain `python -m services.mcp.server` as a standalone entry point. MCP is an adapter,
not another persistence or business-logic layer. Booking calls the existing database
`book_slot()` function through `PostgresStore.book`; stock lookup calls the configured
W3 adapter.

## Security model

- Require the same demo bearer tokens as the API and an exact `X-Agent-Id` match from
  `MCP_APPROVED_AGENTS` on every HTTP request. Missing/unapproved agents are rejected
  and audited. An agent ID is an allowlist label, not a credential.
- Derive the patient from the bearer token. Only front desk can book for another patient,
  only with explicit `on_behalf_of`, and the event records the staff actor and patient.
  Doctor/ops roles cannot book. `check_stock` is front desk/doctor only; booking status
  is owner/staff scoped.
- Reuse the API `RateLimiter`, use the database booking function's idempotency, return
  minimal results, and write an audit event and an argument-free MCP span for each call.
- Keep Streamable HTTP DNS-rebinding protection enabled; configure allowed hosts/origins
  for the deployment. These controls still depend on forgeable demo auth and are not
  production identity assurance.

## SDK version

The architecture pack names `mcp` 2.3.x. On 2026-11-03, the official PyPI JSON endpoint
reported 2.3.0 as the latest release; `mcp==2.3.0` was installed and verified in the
project virtual environment. Pin 2.3.0 in `requirements.txt` to make the tested SDK and
transport API reproducible. It is the newest version verified on PyPI from this machine
and satisfies the pack's 2.3.x line.

## Consequences

- Approved agent hosts discover typed tools without receiving a second scheduling or
  inventory implementation.
- The mounted server shares the API's patient/IP token buckets; standalone mode uses
  the same limiter implementation and limits but has separate in-process buckets.
- MCP calls join the existing append-only audit and Postgres trace system. Trace IDs
  remain sensitive correlators, and spans do not contain arguments.
- Production rollout still requires a trusted identity provider, agent enrollment,
  approved host/origin configuration and distributed rate limiting.

## References

- [MCP tool and client guide](../mcp.md)
- [Container view](../diagrams/container.md)
- [Booking sequence](../diagrams/seq-mcp-booking.md)
- [D7 threat model](../d7-threat-model.md)

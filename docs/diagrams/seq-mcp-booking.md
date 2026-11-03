# MCP booking sequence

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Approved hospital agent
    participant MCP as MCP Streamable HTTP / FastAPI
    participant Auth as Bearer + agent allowlist
    participant Limit as Shared API rate limiter
    participant Store as Scheduling store
    participant DB as Postgres book_slot()
    participant Audit as Audit + trace exporter

    Agent->>MCP: tools/call book_slot(slot_id, idempotency_key)
    MCP->>Auth: Check Bearer token and X-Agent-Id
    Auth-->>MCP: Authenticated user, role, agent ID
    MCP->>Limit: Consume patient and IP book buckets
    Limit-->>MCP: Allowed
    alt Patient role
        MCP->>Store: book(token patient, slot, key)
    else Front-desk role
        MCP->>Store: book(on_behalf_of patient, slot, key, front_desk)
    end
    Store->>DB: SELECT book_slot(patient, slot, key, booked_by)
    DB-->>Store: Existing or newly created booking ID
    Store-->>MCP: Booking ID
    MCP->>Audit: Write actor, role, agent, tool and trace ID
    MCP->>Audit: Export one static-name tool span (no arguments)
    MCP-->>Agent: Minimal booking ID
```

The patient actor comes from the bearer token. A front-desk booking for another patient
requires explicit `on_behalf_of` and records both staff actor and patient in the audit
event. A failed authorization or unapproved agent is denied and audited.

# W1 booking sequence

Sources: `services/api/main.py`, `services/api/deps.py`, `services/agent/w1/{graph,nodes}.py`,
`services/agent/store.py`, migrations 001 and 006. Clarification is conditional; a confident initial
match skips that interrupt. Each resume has its own valid `Idempotency-Key`.

```mermaid
sequenceDiagram
    actor B as Browser
    participant API as API
    participant A as Auth and rate limit
    participant DB as Postgres request map and scheduling
    participant W as W1 graph
    participant C as Postgres checkpointer
    participant E as Egress adapter
    participant M as Model - OpenRouter
    participant S as SMS outbox intent - audit only
    B->>API: POST /w1/triage + bearer + Idempotency-Key K1
    API->>A: Demo auth; validate key; read limit
    API->>DB: Claim (user,K1) before graph/model (006)
    alt Existing claim
        DB-->>API: Existing thread_id
        alt running=true
            API-->>B: 202 running + same thread_id
        else Finished graph invocation
            API->>C: Read current state and interrupts
            API-->>B: Current pause or completed result; no rerun
        end
    else New claim
        API->>W: Invoke with authenticated patient and thread_id
        W->>C: Persist graph steps
        W->>W: Intake, language heuristic, rules-only red flags
        Note over W,M: Rules hit ends in handoff with NO model call; records-only question redirects
        par Clear rules - red-flag net
            W->>E: Net prompt
            E->>M: Regex scrub; refuse if known identifier survives
            M-->>W: Add-only flags via adapter
        and Specialty match
            W->>E: Match prompt
            E->>M: Scrubbed text
            M-->>W: Specialty and confidence via adapter
        end
        opt Low confidence, first turn
            W->>C: Interrupt clarify
            W-->>API: Clarification pause
            API->>DB: Mark running=false
            API-->>B: needs_input + question
            B->>API: Resume answer + K2
            API->>A: Ownership check and read limit
            API->>W: Command resume; key K2
            W->>W: Append clarification; rules run again before models
            Note over W: Second low-confidence turn goes to front desk
        end
        W->>DB: Shortlist doctors and next available slots
        W->>C: Interrupt present_and_pick
        W-->>API: Offered choices
        API->>DB: Finish initial claim if not already finished
        API-->>B: needs_input + pick_slot
        B->>API: Resume offered slot + K3
        API->>A: Owner, replay, pause, booking limit, offered-slot checks
        API->>W: Command resume; booking key K3
        W->>DB: book_slot(patient,slot,K3)
        DB->>DB: Existing key returns appointment; otherwise atomic capacity update + insert
        DB-->>W: appointment id
        W->>DB: Booking audit event
        W->>S: audit(write,sms_outbox,appointment,template)
        W->>C: Persist completion
        W-->>API: Booking confirmation
        API-->>B: done + appointment id
        B->>API: Retry K3
        API-->>B: Same result from process replay or completed booking state
    end
```

Migration 006 reserves a durable `(user_id,idempotency_key)` mapping, with unique `thread_id`,
`running` state and the request fingerprint added by migration 014. Reusing the key for a
different triage body returns 409. W1 checkpoint and resume replay state are shared through
Postgres; a 15-minute lease permits retry after a stale in-progress resume. Failure before
`finish()` can still strand the initial triage reservation; automated recovery is not built.
SQL booking idempotency prevents duplicate appointments across replicas.
Slot-full retries shortlist, then front desk after two booking failures. The SMS participant is an
audit resource name, not a SQL outbox table or a delivery worker; notification delivery is planned Pro.

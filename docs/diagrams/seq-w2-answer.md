# W2 cited answer sequence

Sources: `services/api/main.py`, `services/agent/w2/{graph,nodes,tools,rules}.py`,
`services/records/retrieve.py`. Grade is a conditional edge, not a graph node.

```mermaid
sequenceDiagram
    actor B as Browser
    participant API as API
    participant A as Auth and read limit
    participant W as W2 graph
    participant DB as Postgres consent, records, audit
    participant EMB as Local bge-m3
    participant E as Egress adapter
    participant M as OpenRouter
    B->>API: POST /w2/ask + bearer + question + optional subject_id
    API->>A: Authenticate actor; patient and IP read buckets
    API->>W: Actor from auth; subject defaults to actor
    W->>DB: For caregiver: consent_valid(actor,subject,records)
    W->>W: Scope question guard
    Note over W,DB: Denied consent stops before search; denial audited
    W->>W: Doctor-question deterministic rule
    Note over W,DB: Medical judgement or dose question stops; doctor_review escalation audited
    loop Initial k=6; widen once to k=12 if weak
        W->>EMB: Embed question locally
        W->>DB: Vector + full-text + final fetch: SQL patient_id filter
        Note over DB: Injection-flagged chunks excluded; abbreviations and RRF fusion
        DB-->>W: Subject's chunks with document/page metadata
        W->>DB: Read audit for EACH distinct document returned this pass
        W->>W: Grade: vscore >= 0.45 OR topic coverage > 0.5
    end
    alt Still weak or empty
        W-->>API: not_found; no model call
    else Relevant context
        W->>E: Question + labelled excerpts
        E->>E: Regex scrub; refuse-to-send if known identifier survives
        E->>M: Scrubbed prompt only
        M-->>W: JSON answer and citation labels via adapter
        alt Model error or insufficient=true
            W-->>API: not_found
        else Answer
            W->>W: Resolve citations; verify subject, numbers, diagnosis/dose guard
            alt Invalid answer with usable citations
                W->>W: Verbatim cited lines fallback; verified=false
            else No usable citations
                W->>W: not_found
            end
            W-->>API: Reply + document title/date/page sources
        end
    end
    API-->>B: route, reply, verified, trace_id
```

Auditing happens before grading and model generation, including widened retrieval. It records every
distinct returned document per pass, not every vector candidate examined internally by SQL. The
verifier is a heuristic: number membership uses substring checks and wording checks use regexes;
it does not prove semantic entailment. Verbatim fallback is not a second model call. Foreign cited
chunks raise an error; they are never returned to the browser. No W2 checkpoint is configured in production.

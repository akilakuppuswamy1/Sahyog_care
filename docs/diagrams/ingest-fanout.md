# Bounded document-ingest fan-out

The queue worker claims a bounded batch with `FOR UPDATE SKIP LOCKED`, then invokes
the records ingest graph under one trace. Each `Send` task owns one PDF and one child
span. A per-document failure is recorded and retried without cancelling sibling tasks.
The join waits for every document before the worker marks each job done or schedules
its own retry. The W2 answer graph is unchanged.

```mermaid
flowchart LR
    Q[(ingest_job)] -->|claim up to INGEST_BATCH_SIZE| W[Queue worker]
    W --> P[Plan batch]
    P -->|LangGraph Send; max_concurrency| A[Document A]
    P -->|LangGraph Send; max_concurrency| B[Document B]
    P -->|...| N[Document N]
    subgraph PerDocument["one task and child span per document"]
        A --> AP[Parse PDF]
        AP --> AI[Strip identity block]
        AI --> AC[Chunk and flag injection / PHI]
        AC --> AE[Local bge-m3 embed]
        AE --> AW[Write chunks and status]
        AW --> AA[One document audit event]
    end
    B --> BP[Same per-document pipeline]
    N --> NP[Same per-document pipeline]
    AA --> J[Join after all tasks]
    BP --> J
    NP --> J
    J --> U[Complete or retry each job independently]
    U --> Q
    W -. one parent trace .-> T[(Postgres trace_span)]
    A -. child trace .-> T
    B -. child trace .-> T
    N -. child trace .-> T
```

`INGEST_MAX_CONCURRENCY` defaults to 4; `INGEST_BATCH_SIZE` defaults to 8. The local
embedding model is loaded once by the worker process and shared across its graph tasks.
PDF content hashes make completed ingestion idempotent.

# Containers — C4 level 2

The API uses a shared lock; durable PDF ingestion runs in a separate worker process
with the same image. Sources: `services/api/{main,deps,auth,ratelimit}.py`,
`services/agent/llm.py`, `services/records/{embed,ingest,retrieve}.py`, `infra/docker-compose.yml`.

```mermaid
flowchart TB
    UI[Browser patient, front desk, ops and doctor UI - built] --> API
    subgraph India[India boundary - target hosting; local demo today]
        API[API - built: W1/W2/W3 HTTP routes, front desk, ops, upload]
        AUTH[Auth - built demo bearer roles; OIDC and booking OTP planned Pro]
        RL[Rate limiter - built per patient and IP in process]
        GRAPH[Graph runtime - built W1/W2/W3 LangGraph]
        PG[(Postgres 16 + pgvector + W1 checkpointer + trace_span - built)]
        EMB[Local bge-m3 - built; HashEmbedder in tests]
        ING[PDF upload - enqueue and owner-only job status]
        EG[Egress adapter - Presidio + regex refusal + local restoration]
        AUD[Audit - built explicit writes + append-only trigger + access view]
        TRACE[Tracing - built exporter interface; Postgres in-region]
        PHI[Local ephemeral token maps - built]
        QUEUE[Postgres ingest_job queue - built]
        WORKER[Ingest worker - local embedder; 3 attempts; crash recovery]
        REDIS[Gateway and Redis distributed limits - planned Architect]
        CRYPTO[Encryption at rest and DB least-privilege roles - planned Architect]
        SMS[SMS gateway delivery - planned Pro]
        API --> AUTH
        API --> RL
        API --> GRAPH
        API --> ING
        WORKER --> EMB
        ING --> PG
        GRAPH --> PG
        GRAPH --> EMB
        GRAPH --> EG
        GRAPH --> AUD
        API --> TRACE
        GRAPH --> TRACE
        TRACE --> PG
        AUD --> PG
        PHI --> EG
        ING --> QUEUE
        QUEUE --> PG
        WORKER --> QUEUE
        WORKER --> PG
        RL -.-> REDIS
        CRYPTO -.-> PG
        GRAPH -.-> SMS
    end
    EG -->|Typed tokens only| OR[OpenRouter - external integration built]
```

W1 and W3 use `PostgresSaver` in the production factory; W2 is request-scoped without a
checkpointer. Traces contain no prompt/question/symptom text or patient ID; audit events provide
the trace-to-patient mapping. The exporter interface permits a future self-hosted Langfuse exporter;
Langfuse Cloud is rejected for residency. Uploads are local files, not a built object store. Audit is
not an automatic database read interceptor. Planned tier allocations here and in D7 are delivery
targets, not implemented guarantees.

Migration 012 adds owner-scoped, content-hash-idempotent document jobs. The API
commits document registration and enqueue together and returns HTTP 202. Workers
claim using `FOR UPDATE SKIP LOCKED`; session advisory locks fence live attempts
and disappear on disconnect. Retry eligibility is persisted with exponential
backoff. Indexed documents survive a crash before job completion without another
ingest. Compose shares upload files between API and worker; W3 timeout scheduling
remains planned. Ops queue metrics read `ingest_job` and omit an empty table.

The adapter uses pinned local en_core_web_sm and custom Indian recognisers for W1/W2/W3, including hedges. Maps never leave process memory; replies restore locally. Migration 011 stores numeric deid_ms only. Replay egress-20261103-010825 reports zero hits in 200 synthetic payloads; statistical names/quasi-identifiers remain residual risks.

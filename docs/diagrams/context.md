# Context — C4 level 1

Built interactions: `services/api/main.py`, `services/agent/store.py`, `services/agent/w2/tools.py`.
Boundary is the target in [D6](../../infra/deployment-adr.md), not evidence of deployed India hosting.

```mermaid
flowchart LR
    P[Priya - patient] --> SCC
    M[Meena - caregiver] --> SCC
    F[Front desk] --> SCC
    D[Doctor - built text scribe console] --> SCC
    subgraph India[India trust boundary - target deployment]
        SCC[Sahyog Care Companion]
        SCH[Sahyog scheduling - built SQL tools]
        REC[Sahyog records - built ingest and retrieval]
        STK[Sahyog stock - built CSV adapter; live API stub unconfigured]
        SMS[Sahyog SMS gateway - planned Pro; Core audits intent only]
        EG[Egress adapter - built Presidio, typed tokens and regex refusal]
        SCC --> SCH
        SCC --> REC
        SCC --> STK
        SCC -.-> SMS
        SCC --> EG
    end
    EG -->|Only scrubbed text; no token map| OR[OpenRouter - OUTSIDE India boundary]
    OR -->|Model response| EG
```

Meena's records access requires current, scoped consent. The egress arrow states the intended contract:
statistical recognition retains risks for unfamiliar names and quasi-identifiers; see [D7](../d7-threat-model.md).
No external HIS, live pharmacy feed, SMS delivery is claimed as built.

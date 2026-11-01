# Data contract: doc_chunk

```
table: doc_chunk
owner: Medical records lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Indexed within 60 s of upload
sensitivity: clinical
checks: (doc_id, patient_id) matches document (composite FK); page >= 1; embedding 1024 dims; >= 5 candidate chunks per eval case
consumers: W2 retriever (hard patient filter), citation checker
source: Derived from document by the ingest graph
```

Compliance sign-off on the sensitivity class: _pending_

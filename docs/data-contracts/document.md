# Data contract: document

```
table: document
owner: Medical records lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Status changes within 60 s of upload (E3)
sensitivity: clinical
checks: type in allowed set; ingest_status in {queued, parsing, indexed, failed}; patient_id exists
consumers: W2 ingest and ask graphs, doc_chunk
source: Runtime uploads; 2,000 generated report PDFs for the demo
```

Compliance sign-off on the sensitivity class: _pending_

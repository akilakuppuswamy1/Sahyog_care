# Data contract: patient

```
table: patient
owner: Patient registration lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Real time at registration; FHIR demographics cached per session (D3 A01)
sensitivity: direct
checks: patient_id unique; dob not in future; language BCP-47; abha_id unique when present; phone matches Indian mobile format
consumers: every table with patient_id; the egress scrubber token map
source: Generated master seeded from Healthcare Dataset encounter keys (names never copied)
```

Compliance sign-off on the sensitivity class: _pending_

# Data contract: doctor

```
table: doctor
owner: Medical administration lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Nightly; a doctor added today may be missing until tomorrow
sensitivity: direct (name) / operational (rest)
checks: 500 rows; >= 10 per specialty; >= 1 per clinic; names unique; languages BCP-47 with en first; synthetic = true
consumers: W1 shortlist tool, slot, encounter, clinical_note signer check
source: Generated, data/synthetic/generate_doctors.py (doctors-v1, seed 20261102)
```

Compliance sign-off on the sensitivity class: _pending_

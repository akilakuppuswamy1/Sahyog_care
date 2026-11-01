# Data contract: prescription

```
table: prescription
owner: Pharmacy lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Written with the signed note
sensitivity: clinical
checks: note_id exists; medicine_text present; substitute_ok defaults false and only the pharmacist sets it
consumers: pharmacy counter, stock reservation, patient notification
source: Runtime from signed notes
```

Compliance sign-off on the sensitivity class: _pending_

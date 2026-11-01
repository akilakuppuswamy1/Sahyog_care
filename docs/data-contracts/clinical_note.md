# Data contract: clinical_note

```
table: clinical_note
owner: Chief medical officer (role)
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Written once at signature; immutable
sensitivity: clinical
checks: signed_at NOT NULL; signed_by = doctor_id; S/O/A/P keys present; content_hash = sha256(soap_json); (encounter, version) unique
consumers: patient record view, prescription, pharmacy, audit
source: Runtime via W3 interrupt resume only
```

Compliance sign-off on the sensitivity class: _pending_

# Data contract: appointment

```
table: appointment
owner: Front desk lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Real time
sensitivity: operational, tied to a patient so audited
checks: (patient_id, idempotency_key) unique; one live booking per patient per slot; status in allowed set
consumers: front-desk queue, SMS notifier, no-show analytics, encounter
source: Runtime via book_slot(); history from No Shows (110,527 rows est.)
```

Compliance sign-off on the sensitivity class: _pending_

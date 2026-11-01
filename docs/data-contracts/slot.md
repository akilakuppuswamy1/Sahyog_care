# Data contract: slot

```
table: slot
owner: Front desk lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: 30 s cache (deck p.31); source of truth is the DB row
sensitivity: operational
checks: 0 <= booked <= capacity; (doctor_id, starts_at) unique; starts_at inside clinic hours; 480,000 rows for 60 working days
consumers: W1 shortlist and booking; front-desk queue
source: Generated, data/synthetic/generate_slots.py (slots-v1), plain code
```

Compliance sign-off on the sensitivity class: _pending_

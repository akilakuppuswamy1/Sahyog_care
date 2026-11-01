# Data contract: clinic

```
table: clinic
owner: Network operations lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Changes when a site opens or closes; read daily is enough
sensitivity: operational
checks: clinic_id unique and matches ^(PUN|HYD)-[HC]\d{2}$; closes_at > opens_at; 28 rows (6 hospitals, 22 clinics)
consumers: slot (hours bound the calendar), medicine_stock, doctor; a renamed id breaks every FK
source: Team-authored reference file data/reference/clinics.csv; fictional
```

Compliance sign-off on the sensitivity class: _pending_

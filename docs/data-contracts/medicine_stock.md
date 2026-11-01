# Data contract: medicine_stock

```
table: medicine_stock
owner: Pharmacy lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Nightly CSV must land before 08:00 IST; older than 24 h shows as 'unknown' (D3 A02)
sensitivity: operational
checks: qty >= 0; price_inr >= 0; as_of present; source in {csv, api}; (medicine_id, clinic_id) unique
consumers: W3 stock tool, pharmacist view; API swap must keep this shape
source: A-Z Medicine Dataset of India formulary + generated nightly per-clinic quantities
```

Compliance sign-off on the sensitivity class: _pending_

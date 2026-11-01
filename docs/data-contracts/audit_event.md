# Data contract: audit_event

```
table: audit_event
owner: Compliance officer (role)
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Real time; retained 1 year
sensitivity: quasi (ids only, no clinical text)
checks: append-only (UPDATE/DELETE raise); actor_role and action in allowed sets; purpose NOT NULL
consumers: who_saw_record view (E6-1), incident review, dashboards
source: Runtime, written by every service
```

Compliance sign-off on the sensitivity class: _pending_

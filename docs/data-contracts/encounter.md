# Data contract: encounter

```
table: encounter
owner: Medical records lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Written at consultation start
sensitivity: clinical
checks: ended_at >= started_at (date-order violations = 0); one encounter per appointment
consumers: W3 scribe, clinical_note, pre-consult brief
source: Runtime; history from Healthcare Dataset (prasad22), names dropped
```

Compliance sign-off on the sensitivity class: _pending_

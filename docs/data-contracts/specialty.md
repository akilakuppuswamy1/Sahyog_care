# Data contract: specialty

```
table: specialty
owner: Clinical safety lead
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Static between releases
sensitivity: operational
checks: 38 rows; name unique; red_flag_rules is valid JSON array
consumers: W1 match node and crosswalk; doctor; a removed specialty strands doctors and eval cases
source: Team-authored data/reference/specialties.csv
```

Compliance sign-off on the sensitivity class: _pending_

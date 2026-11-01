# Data contract: consent

```
table: consent
owner: Compliance officer (role)
schema_version: 1 (change rule: additive columns by PR with a migration; a rename or type change needs an ADR and every consumer below signed off)
freshness: Real time: checked on every read via consent_valid()
sensitivity: quasi
checks: expires_at > granted_at; scope within {records, appointments, signed_notes}; grantee <> patient
consumers: W2 retrieval guard, appointment views; a change to scope values breaks the access matrix
source: Runtime, written by the patient app (E3-2)
```

Compliance sign-off on the sensitivity class: _pending_

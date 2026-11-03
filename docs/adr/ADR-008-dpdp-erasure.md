# ADR-008: DPDP consent withdrawal and upload erasure

Date: 3 Nov 2026
Status: Implemented in this change

## Context

CR-001 is self-set; no change request was issued by the client. It exercises the
day-8 re-plan for patient-controlled caregiver consent withdrawal and erasure of
patient-uploaded reports. The caregiver must be refused on the next W2 request.
Erasure must remove the source upload and all derived content without allowing a
patient to affect another patient's records or delete a path outside the upload
root. Hospital-issued reports and signed W3 notes remain protected and return
HTTP 409 with a reason.

DPDP Act, 2023 sections 6(4) (withdrawal of consent) and 12 (erasure) are the
basis for the requested behavior; **compliance officer to confirm**
interpretation, scope, retention exceptions, and audit-record treatment before
production use.

## Options

1. **Absorb everything, including backups:** ~2 days; misses the deadline.
2. **Trade (chosen):** consent withdrawal and upload erasure now, ~4 h; backup
   erasure and a records-office flow go to the roadmap; nothing in the Pro scope
   is cut.
3. **Defer all:** no cost now; fails DPDP at go-live.

## Decision

Choose **option 2: trade**. Provide an authenticated patient's document list,
upload erasure, and caregiver-consent grant/withdraw endpoints. Patient identity
comes only from the bearer token; caregiver W2 context is bound to the caregiver
token and cannot be selected in the request body. Check current consent on every
W2 request so the first request after withdrawal is refused.

Permit erasure only for patient-uploaded documents. Serialize against ingestion,
resolve the file path beneath the configured upload root, and remove the file,
document, chunks/embeddings, document PHI token map, and ingest-job link. Write
an append-only audit event without PHI. Refuse hospital-issued reports and
signed W3 notes with HTTP 409 and a reason; a foreign patient's document returns
403. Preserve audit history and do not claim backup erasure.

## Consequences

- Regression cases precede implementation and verify post-erasure retrieval,
  caregiver refusal, protected-record responses, complete cleanup, audit events,
  trace hygiene, upload-root confinement, and cross-patient denial.
- Erasure and the ingest worker must be serialized so an in-flight job cannot
  recreate erased content.
- A request already past the consent access check cannot be recalled.
- Backup erasure and the records-office flow move to the roadmap; no Pro scope
  is cut.
- This is an implementation decision, not a legal conclusion.

## Trigger to revisit

Revisit after compliance review, when the client confirms hospital-report
retention and backup-erasure expectations, if legal holds require a different
protected-record policy, or if the CMO identifies a week-one stop condition.

## References

- [CR-001](../change-requests/CR-001-dpdp-erasure.md)
- Request: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372146
- Same-day reply: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372154
- Thread: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1

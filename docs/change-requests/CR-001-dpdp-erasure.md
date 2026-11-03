Self-set: no change request was issued to this pod, so this one was chosen to exercise the day-8 re-plan (pack p.43). Type: regulation. Raised and answered 3 Nov 2026.

Request: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372146 · Same-day reply: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372154 · Thread: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1

# CR-001 — DPDP consent withdrawal and upload erasure

Date: 3 Nov 2026 · Status: Implemented in this change. · Decision: option 2 (trade), [ADR-008](../adr/ADR-008-dpdp-erasure.md)

## Request text

The following is a **pod-written client-voice restatement for this exercise, not a quotation or request issued by Sahyog**:

> "Before go-live, a patient must be able to withdraw a family member's access to their records at any time, and to have any report they uploaded erased — as the Digital Personal Data Protection Act 2023 requires. Tell us what this changes and what moves."

In one sentence: a patient can withdraw a caregiver's access at any time and erase reports they uploaded, with every action audited and hospital-issued records left protected.

## Impact

| Area | Impact |
|---|---|
| Scope and API | List the authenticated patient's own documents; erase an eligible upload; grant and withdraw caregiver consent. |
| Data model | Mark upload provenance and remove the upload, document row, chunks/embeddings, document PHI token map, and ingest-job link. |
| Architecture | Serialize erasure with ingestion, resolve paths beneath the upload root, and audit/trace without PHI. |
| Protected records | Hospital-issued reports and signed W3 notes remain immutable through this API and return HTTP 409 with a reason. |
| Audit and traces | Erasure and withdrawal are audited; traces contain no PHI. |
| Evaluation | Add regression cases before implementation in `evals/sets/dpdp_erasure.jsonl` and `services/api/tests/test_erasure_postgres.py`. |

## Options

| Option | Outcome | Cost | Delivery / trade |
|---|---|---|---|
| 1. Absorb | Absorb everything, including backups. | ~2 days | Misses the deadline. |
| 2. TRADE (chosen) | Deliver consent withdrawal and upload erasure now. | ~4 h | Backup erasure and a records-office flow go to the roadmap; nothing in the Pro scope is cut. |
| 3. Defer all | Record the requirement and make no change now. | No cost now | Fails DPDP at go-live. |

## Decision

Choose **option 2 (TRADE)**, recorded in [ADR-008](../adr/ADR-008-dpdp-erasure.md).
Patient identity comes from the bearer token only. Withdrawal is checked on the
caregiver's next W2 request. Erasure is limited to patient-uploaded reports under
the configured upload root. Hospital-issued reports and signed W3 notes return
409 with the reason; a foreign patient's document returns 403.

DPDP Act, 2023 sections 6(4) (withdrawal of consent) and 12 (erasure) are the
basis for the requested behavior; **compliance officer to confirm** the
interpretation, scope, retention exceptions, and audit-record treatment before
production use.

## What moved

- Backup erasure and the records-office flow move to the roadmap.
- Nothing in the Pro scope is cut.
- The report and memo refresh moved to the following day.
- The new regression cases were added before the implementation.

## Open questions

- What is the retention period for hospital-issued reports, and which legal holds
  require a 409 refusal?
- May backups expire on their normal cycle, or must they be erased on request?
- Which audit metadata must remain after erasure, and for how long?
- Does withdrawal apply to all caregiver scopes and already-running W2 work, or
  only requests that start after the withdrawal commits?
- What week-one condition would make the CMO turn this feature off? See D2 Q3
  and assumption A20.
- Compliance officer to confirm the DPDP sections 6(4) and 12 interpretation.

## Client thread

- Request: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372146
- Same-day reply: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19372154
- Thread: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1

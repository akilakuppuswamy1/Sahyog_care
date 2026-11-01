# D2 — Ten discovery questions

**Status:** draft v0.1 · 2026-11-02 · to be posted in the client thread
**Acceptance (deck p.9):** the team's own ten questions are posted, with at most three taken from p.9. Faculty answer in role within one working day, and each answer lands in `docs/assumptions.md`.

Three questions come from p.9 (marked **[pack]**). The other seven are the team's own.

| # | Question to the client | Decision it unlocks | Assumption row |
|---|---|---|---|
| Q1 | **[pack]** What must happen when a patient reports chest pain at night? Who is on the emergency desk, and how fast do they call back? | The red-flag rule list, the escalation path and the "nurse calls within 2 min" promise in the reply text | A09 |
| Q2 | **[pack]** Which system is the source of truth for the appointment book: the HIS, or a separate scheduler? | Whether we write bookings directly or propose them for front-desk confirmation | A10 |
| Q3 | **[pack]** What would make the CMO switch this off in week one? | The first demo and the first guardrail | — |
| Q4 | The SMS gateway needs the patient's phone number, a direct identifier. Is that gateway an India-hosted, DLT-registered provider under contract, and does it count as inside the hospital boundary? | Whether the SMS path is an allowed exception to "identifiers never leave" or needs a hospital-run relay | A08 |
| Q5 | May consultation audio be sent to a hosted speech-to-text service in an India region, or must transcription run on hospital hardware? | Local faster-whisper vs a hosted STT, and where the scribe service sits in the container view | A11 |
| Q6 | When a caregiver is granted access, is the scope per document, per document type, or the whole record? Can it include appointments as well as reports? | The consent schema (`scope` values) and the access matrix row "Appointment book, Own ward's" | A12 |
| Q7 | Do clinics overbook deliberately to absorb the 18% no-shows? If so, by how much per slot? | Slot `capacity` > 1 vs a free/taken flag, and whether the no-show signal adjusts capacity | A05 |
| Q8 | When the nightly stock CSV is late or missing, what may a doctor be shown: yesterday's stock with its age, or "unknown"? What is the maximum acceptable staleness? | The stock adapter's freshness rule and the flag wording | A02 |
| Q9 | Does the LLM vendor agreement guarantee zero data retention and no training on our de-identified payloads? Which vendor regions are acceptable? | The egress ADR, the vendor shortlist and the fallback provider | A07 |
| Q10 | Who owns an incident when the assistant misleads a patient: clinical governance, IT or the front desk? What response time is expected? | The review-queue owner, its SLA, and who receives the audit export | A13 |

## Posting note
Post as one message, numbered as above. Ask for answers within one working day. When an answer arrives, update the matching assumption row and set its status to `validated` or `changed`.

Posted to the client thread: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1 (3 Nov 2026)

# D2 — ten discovery questions (v0.2). Posted to the client thread https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1 on 3 Nov 2026. Q1–Q3 are from pack p.9; Q4–Q10 are the pod's own. No client answers have been received; each question is closed with a working assumption in docs/assumptions.md (assumption, how to check, fallback).

| # | Question | Decision it unlocks | Assumption row |
|---|---|---|---|
| Q1 | What must happen when a patient reports chest pain at night? Who is on the emergency desk, and how fast do they call back? | The red-flag rule list, the escalation path and the call-back promise in the reply text | A09 |
| Q2 | Which system is the source of truth for the appointment book: the HIS, or a separate scheduler? | Whether we write bookings directly or propose them for front-desk confirmation | A10 |
| Q3 | What would make the CMO switch this off in week one? | The first demo and the first guardrail | A20 |
| Q4 | The SMS gateway needs the patient's phone number, a direct identifier. Is that gateway an India-hosted, DLT-registered provider under contract, and does it count as inside the hospital boundary? | Whether the SMS path is an allowed exception to "identifiers never leave" or needs a hospital-run relay | A08 |
| Q5 | May consultation audio be sent to a hosted speech-to-text service in an India region, or must transcription run on hospital hardware? | Local vs hosted speech-to-text, and where the scribe service sits in the container view | A11 |
| Q6 | When a caregiver is granted access, is the scope per document, per document type, or the whole record? Can it include appointments as well as reports? | The consent schema (scope values) and the caregiver access matrix | A12 |
| Q7 | Your RFP quotes 18% no-shows, but the appointment data we were given shows 20.2%. Which figure and period should we plan with, and do clinics deliberately overbook to absorb no-shows — if so, by how much per slot? | Slot capacity > 1 vs a free/taken flag, and whether a no-show signal adjusts capacity | A05 |
| Q8 | When a prescribed brand is out of stock at the doctor's clinic, may the assistant suggest a same-molecule alternative that is in stock, or must it only flag the gap and leave the choice entirely to the doctor? | The scribe's stock-check behaviour and the wording of the stock flag | A02 |
| Q9 | Does the LLM vendor agreement guarantee zero data retention and no training on our de-identified payloads? Which vendor regions are acceptable? | The egress ADR, the vendor shortlist and the fallback provider | A07 |
| Q10 | Patients write in English and Hinglish today. By when must Hindi in Devanagari and Marathi be supported, and who at Sahyog reviews the assistant's replies in those languages before go-live? | The language-detection node, the eval cases per language and the reviewer for translated replies | A03 |

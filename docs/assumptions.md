# D3 — Assumptions register

**Status:** draft v0.1 · 2026-11-02
**Acceptance (deck p.10):** at least eight rows, each with an owner, a validation and a fallback.

Owners are roles, not people. Status is one of `open`, `validated`, `changed` or `failed`.

| ID | Assumption | Owner | How we validate it | Fallback if it fails | Linked to | Status |
|---|---|---|---|---|---|---|
| A01 | The HIS FHIR API answers a demographics read within 300 ms. | Integration lead | Time 50 calls against a mock with injected latency (FHIR is optional, deck p.4) | Cache demographics per session; degrade to manual lookup | E6 | open |
| A02 | The nightly stock CSV is complete before clinics open. | Data lead | Check file timestamps across a week of samples | Show stock age on every flag; mark "unknown" when stale | D2 Q8, E5-3 | open |
| A03 | Patients type symptoms in English or mixed Hindi-English. | ML lead | Generate 200 synthetic intakes in both styles and run them through matching | Language-detection node before the specialty match | T16, E1-1 | open |
| A04 | A doctor reviews the draft during the consultation itself. | Product lead | Role-play 5 consultations with faculty as the doctor | Queue drafts for end-of-session review (T19) | E5-6 | open |
| A05 | One slot maps to exactly one appointment. | Product lead | Ask front desk whether overbooking is practised | Model capacity per slot instead of a free/taken flag | D2 Q7, E2-2 | open |
| A06 | De-identified text is enough for a good SOAP draft. | ML lead | Compare drafts on 30 transcripts with and without identifiers (field-level F1) | Keep identified drafting on-premise with a local model | E5-1 | open |
| A07 | The LLM vendor contract gives zero retention and an India-acceptable region. | Platform lead | Read the vendor terms; confirm in the client thread | Hybrid deployment with an in-region hosted model for clinical text | D2 Q9, D6 | open |
| A08 | The SMS gateway is India-hosted and contracted, so a phone number may reach it. | Platform lead | Client confirms the provider and its DLT registration | Hospital-run SMS relay; the Companion sends a token, not the number | D2 Q4, E2-1 | open |
| A09 | An emergency desk is staffed 24×7 and can call back within 2 minutes. | Clinical safety lead | Client confirms staffing and SLA | Reply text drops the "2 minutes" promise; it says call 112 and gives the ED address only | D2 Q1, E1-2 | open |
| A10 | The appointment book can be written by the Companion, not only proposed. | Integration lead | Client names the source of truth | Bookings become proposals in the front-desk queue; the patient is confirmed after approval | D2 Q2, E2 | open |
| A11 | Speech-to-text can run on hospital hardware within the 20 s draft budget. | ML lead | Time faster-whisper 1.2.1 on 10 synthetic 8-minute recordings (Pro tier) | In-region hosted STT under contract; audio still deleted in 24 h | D2 Q5, E5 | open |
| A12 | Caregiver consent is scoped per document type with an expiry. | Compliance lead (role) | Client answers D2 Q6; compliance signs the access matrix | Whole-record scope with a shorter expiry and a mandatory purpose field | D2 Q6, E3-2 | open |
| A13 | A named role owns incidents and works the review queue within a stated SLA. | Product lead | Client answers D2 Q10 | Default owner is the front desk lead, with a 4-hour SLA, flagged as a risk in the memo | D2 Q10, E6-3 | open |
| A14 | The public datasets' licences allow use in this engagement. | Data lead | Record the licence of each source in `data/inventory.md` | Drop the source and replace it with the generated layer; record the gap | D5 | open |
| A15 | OCR on scanned report PDFs keeps character error rate under 5%. | Data lead | Render 50 synthetic PDFs as images, OCR them, compare to the source text | Mark low-confidence pages; answers citing them carry a "scan quality" note | E3-3 | open |

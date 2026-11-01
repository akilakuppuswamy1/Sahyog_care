# PRD — Sahyog Care Companion

| | |
|---|---|
| Status | Draft v0.1 (2026-10-29) |
| Client | Sahyog Health Network (fictional): Pune 3 hospitals + 11 clinics, Hyderabad 3 hospitals + 11 clinics |
| Sources | `Technical Architecture Proposal` (docx), `24_Sahyog Care Companion Engagement` (pptx, page refs below are "deck p.N") |
| Companion docs | [ARCHITECTURE](ARCHITECTURE.md) · [RULES](RULES.md) · [DESIGN](DESIGN.md) · [TASKS](TASKS.md) · [MEMORY](MEMORY.md) |

This file carries both the business requirements (why, who, what success means) and the product requirements (what is built). There is no separate BRD; section 2 is the BRD content.

---

## 1. Summary

Sahyog Care Companion is a privacy-compliant, agentic platform shared across both cities. It does three things:

1. **Find-my-doctor**: symptoms in, a specialty and three doctors out, a confirmed slot booked without a phone call.
2. **My records**: patients upload report PDFs and ask questions; every answer is cited and scoped to their own data.
3. **Consultation scribe**: a doctor's dictation becomes a SOAP draft that is persisted only after the doctor signs.

Underneath sit identity, consent, audit and evaluation as one shared platform.

### Want vs need (deck p.3–4)

The brief is "go by what the client WANT, then work out what they actually NEED". The gap is where the design decisions come from.

| Client wants | Client actually needs | Design consequence |
|---|---|---|
| "An AI that books appointments" | Fewer phone calls *without* a bot that books the wrong slot and sends patients to the counter | Idempotent booking, front-desk override and live queue |
| "Ask questions about my reports" | Nobody else ever reads those reports | Patient filter in SQL, not in the prompt; zero-tolerance scope-leak test |
| "AI-written clinical notes" | Doctors not blamed for a line the model invented | Graph interrupt; unsigned note is a draft by schema; model structures, never prescribes |
| "Use a good LLM" | Identified data stays in India; vendor is swappable | Egress scrubber, model wrapper, hybrid deployment |

---

## 2. Business context and objectives (BRD)

### 2.1 Problem (client's own figures, to be verified in discovery)

| Figure | Operational impact |
|---|---|
| 3.1M outpatient visits/year, 9,000 appointments/day | Front-desk and call-centre overload |
| 41% of bookings by phone | Queue congestion, manual scheduling errors |
| 18% no-show rate | Idle clinical capacity |
| 2.4M scanned report PDFs/year | Diagnostic data siloed from care |
| 11 min of each consultation spent typing | Lost clinical time |

### 2.2 Board mandate: five outcomes

| # | Outcome | Measure |
|---|---|---|
| O1 | Patient finds the right doctor and books in under 3 min, no phone | Median time-to-book < 3 min; ≥ 70% of synthetic sessions complete without a call |
| O2 | Patient sees and questions own records, every answer cited and scoped | Faithfulness ≥ 0.9; scope leaks = 0 |
| O3 | Doctor leaves with a signed note after ≤ 90 s review | Review time < 90 s in role-play |
| O4 | Every AI output traceable, auditable, reversible by a named human | Audit row + trace id for every run |
| O5 | DPDP-compliant, ABDM-ready, path to on-premise for identified data | Zero identified payloads in 200-payload egress replay |

### 2.3 Stakeholders and what each is owed (deck p.7)

| Stakeholder | Fear | First proof owed |
|---|---|---|
| CMO | AI advice harms a patient | Red-flag gate, shown on hard cases |
| CIO | Lock-in, data leak | Container view with egress boundary marked |
| Compliance officer | DPDP breach in month one | Audit log and consent table |
| Doctors | Blamed for an AI note | Proof nothing persists unsigned |
| Front desk | Replacement | Escalation flow and queue view |
| Pharmacy | Software-made substitutions | Stock flags, choice left human |
| Patients | Strangers reading their reports | Scope guard and citations on every answer |

### 2.4 Engagement constraints

Two weeks from kickoff to steering review; pods of three. Acceptance is a steering review plus a five-minute technical defence. Delivery depth is one of three tiers (Core ≈ 38 h, Pro ≈ 58 h, Architect ≈ 80 h); see [TASKS](TASKS.md). The client, people and figures are fictional.

---

## 3. Scope

**In scope**
- Find-my-doctor triage and slot booking (W1)
- Record upload, storage and Q&A (W2)
- Consultation scribe with doctor sign-off (W3)
- Shared platform: identity, consent, audit, evaluation

**Out of scope**
- Billing and insurance adjudication
- Telemedicine video and wearable feeds
- Inpatient (IPD) workflows (future extension only)

**Roadmap options, not commitments** (deck p.52): nursing-note summaries, real-time pharmacy feed, MCP tool server, claims/ICD-10 assist, supervisor-with-specialists. Priced in the memo; an Architect pod builds one.

---

## 4. Personas (deck p.11)

A feature that serves none of these is scope creep.

| Persona | Needs | Fears |
|---|---|---|
| **Priya**, 34, patient and carer for her mother | Right specialist tonight; yesterday's report in plain words | Her mother's reports read by someone who shouldn't |
| **Dr. Rao**, cardiologist, 22 patients/day | A note he can sign in 90 s without re-reading the transcript | Being on record for a line the model invented |
| **Meena**, front desk lead, 300 calls/day | Fewer "which doctor" calls; a queue she controls | A bot that books the wrong slot |
| **Imran**, pharmacist, two counters | Prescriptions matched to stock before the patient walks over | Substitutions decided by software |
| **Kavita**, compliance officer | "Who saw this record" answered with one query | Identified data in a public API log |

---

## 5. Epics, stories and acceptance criteria

Each criterion is written so a test can pass or fail it. "RFP" refers to the addendum answers (deck p.5).

### E1 — Find my doctor (Priya) · W1
Criterion: free-text symptoms return one specialty and three doctors with next free slot within 4 s; any red-flag phrase ends the flow with emergency guidance and a logged human hand-off.

| ID | Story | Notes / RFP line |
|---|---|---|
| E1-1 | As a patient I describe symptoms in free text (English; mixed Hindi-English must not break) | Language-detect node before match |
| E1-2 | As a patient reporting emergency symptoms I am told to call 112 / go to ED and the emergency desk is alerted | Rules fire before any model call; "can the assistant diagnose? No" |
| E1-3 | As a patient I see a specialty, optional alternative, and three doctors with next slots and languages | Confidence < 0.6 → one clarifying question; two low turns → front desk |
| E1-4 | As a patient I never receive a diagnosis | Wording guardrail on every patient-facing reply |

### E2 — Book and manage (Priya, Meena) · W1
Criterion: booking is idempotent under retries, confirms by SMS, and front desk can see and override any assistant-made booking.

| ID | Story |
|---|---|
| E2-1 | As a patient I pick a slot and get an SMS confirmation |
| E2-2 | As the system I never double-book a slot (unique constraint + idempotency key; capacity-based slots) |
| E2-3 | As front desk I see a live queue of assistant bookings and escalations and can override any |
| E2-4 | As a patient I get reminders/confirmations that reduce the 18% no-show rate |

### E3 — My records (Priya) · W2 ingest
Criterion: a text PDF is stored against the patient within 60 s with visible status; a caregiver sees it only while consent is valid.

| ID | Story |
|---|---|
| E3-1 | As a patient I upload a PDF and see its status (queued, parsing, indexed, failed) |
| E3-2 | As a patient I grant a caregiver access with scope and expiry; access lapses automatically |
| E3-3 | As the system I OCR scanned PDFs, tag PHI, chunk by section and index with `patient_id` |

### E4 — Ask my record (Priya) · W2 ask
Criterion: every answer cites document and page; refuses questions outside the patient's own records; hands off to a doctor when asked for a diagnosis or dose.

| ID | Story |
|---|---|
| E4-1 | As a patient I ask a question and get an answer with doc + page citations |
| E4-2 | As a patient I get a refusal for another person's data or out-of-record questions |
| E4-3 | As a patient asking for a dose/diagnosis I am handed to a doctor |
| E4-4 | As compliance I can prove a PDF carrying injected instructions cannot widen scope (red-team PDF) |

### E5 — Scribe (Dr. Rao, Imran) · W3
Criterion: transcript becomes a SOAP draft with medicines flagged against stock; draft persists only with the doctor's signature and edit history.

| ID | Story |
|---|---|
| E5-1 | As a doctor I get a SOAP draft after dictation (p95 < 20 s after transcript) |
| E5-2 | As a doctor I edit, sign or reject with comments (reject redrafts); nothing is stored before signature |
| E5-3 | As a doctor I see medicines flagged against stock with stock age; unknown when feed is stale |
| E5-4 | As the system I never invent a molecule: a class named without a molecule yields "[doctor to specify]" |
| E5-5 | As a pharmacist I retain final authority over substitutions |
| E5-6 | As a doctor, if I skip live review the draft parks in the review queue rather than failing |
| E5-7 | As a patient I am notified of tests ordered after the note is signed |

### E6 — Platform (Kavita)
Criterion: every record read writes an audit event with actor and purpose; consent carries scope and expiry; identified data never reaches an external API.

| ID | Story |
|---|---|
| E6-1 | As compliance I answer "who saw this record" with one SELECT |
| E6-2 | As a CIO I can show the egress scrubber and boundary, and swap the LLM vendor without graph changes |
| E6-3 | As an operator I see p95s, red-flag rate, cost per run, review queue and edit distance on one screen |
| E6-4 | As compliance I can show audio deletion within 24 h as an audit event |

---

## 6. Non-functional requirements (deck p.16)

| Requirement | Target |
|---|---|
| Triage turn latency | p95 < 4 s |
| Record answer latency | p95 < 8 s |
| Note draft latency | p95 < 20 s after transcript |
| Availability | 99.5% in clinic hours |
| Throughput | 9,000 appointments/day; 1,400 requests/hour morning peak |
| Audit | Every record read: actor, purpose, time; retained 1 year |
| Residency | Identified data stored and processed in India |
| Retention | Audio deleted ≤ 24 h; checkpoints 30 days |
| Recovery | Restore ≤ 4 h; checkpoints survive restarts |
| Language | English first; schema language-agnostic for Hindi and Marathi |

## 7. Success metrics (demo screen)

| Metric | Target |
|---|---|
| Bookings completed without a call | ≥ 70% of synthetic sessions |
| Median time to book | < 3 min |
| Red-flag recall on eval set | **100%, no exceptions** |
| Specialty accuracy (golden set) | ≥ 85% |
| Answer faithfulness (calibrated judge) | ≥ 0.9 |
| Scope leaks (red-team set) | **0** |
| Doctor review time per note | < 90 s in role-play |
| Cost per consultation | Computed, formula shown, price-list date recorded |

A target that cannot be met is reported with the number reached and the reason, never dropped.

---

## 8. Constraints

- Identified health data never leaves India; only de-identified text crosses the boundary.
- LLM vendor swappable without rewriting graphs.
- HIS exposes FHIR R4 read-only (demographics, encounters). The deck marks FHIR as optional for the engagement, so a mock is acceptable.
- Pharmacy stock: nightly CSV now, API in ~6 months, behind one interface.
- English first; nothing in the schema may assume English.
- Stack: LangGraph 1.2, Postgres + pgvector, FastAPI, MCP.

## 9. Regulatory

DPDP Act 2023: Rules notified 13 Nov 2025; consent-manager rules from 12 Nov 2026; substantive obligations from 12 May 2027 (as cited in the deck, p.55; confirm with counsel before relying). ABDM-ready; ABHA linking is an Architect-tier extension, not Core.

## 10. RFP addendum traceability (deck p.5)

| Addendum answer | Requirement it becomes |
|---|---|
| Can the assistant diagnose? No | Red-flag gate before any match; wording guardrails (E1-2, E1-4, E4-3) |
| Share records with family? Yes, expiring consent | Consent table with scope, grantee, expiry (E3-2) |
| Where does audio go? Deleted within 24 h | Object-store lifecycle + audit event (E6-4) |
| Identified data to public LLM? Only de-identified | Egress scrubber (E6-2) |
| Where does stock come from? CSV now, API later | One interface, two adapters (E5-3) |
| Who approves AI notes? The doctor, every time | Interrupt before persistence (E5-2) |
| Volume? 9,000/day, 1,400/hr peak | Load test, async ingest, stateless API (§6) |

## 11. Top risks (full register in ARCHITECTURE §14)

Cross-patient chunk leak · invented medicine in a draft · missed red flag · slot double-booking · runaway cost · doctors ignoring drafts. Each has an early signal and a built detector (deck p.44).

## 12. Open questions

See [MEMORY.md](MEMORY.md) → "Open questions" and "Source conflicts". The ten discovery questions (D2) are tracked in [TASKS.md](TASKS.md).

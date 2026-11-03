# D1 — Automation ladder (task cards)

**Status:** draft v0.1 · 2026-11-02 · owner: pod lead
**Acceptance (deck p.8):** the 12 client tasks plus at least 6 found by the team, each on one rung with the four answers recorded.

## How a rung is chosen

| Question | Field |
|---|---|
| Is the rule fully known and stable? | `rule_known` |
| Is the input bounded and well formed? | `input_bounded` |
| What does a wrong answer cost, and who notices? | `cost_of_error` |
| Who must sign off before the effect is real? | `sign_off` |

- Two "yes" answers at the top (rule known, input bounded) put a task on **code**.
- A cost measured in patient harm or in law puts it on **human**, whatever the model can do.
- **llm** means one fuzzy step inside checked code.
- **agentic** means the path depends on tool results.

## Summary

| # | Task | Source | Rung |
|---|---|---|---|
| T01 | Check the slot is still free | client | code |
| T02 | Send the booking confirmation | client | code |
| T03 | Convert a PDF to text | client | code |
| T04 | Check whether a medicine is in stock | client | code |
| T05 | Map symptoms to a specialty | client | llm |
| T06 | Answer a question on a report | client | llm |
| T07 | Turn dictation into a SOAP note | client | llm |
| T08 | Decide whether a question needs a doctor | client | llm |
| T09 | Detect red flags (rules first, LLM second) | client | llm |
| T10 | Choose among six candidate doctors | client | agentic |
| T11 | Sign the clinical note | client | human |
| T12 | Pick a substitute medicine | client | human |
| T13 | Check caregiver consent before a read | team | code |
| T14 | De-identify a payload before it leaves the boundary | team | code |
| T15 | Delete consultation audio after 24 h | team | code |
| T16 | Detect the language of a patient intake | team | llm |
| T17 | Extract medicines from a transcript | team | llm |
| T18 | Override an assistant-made booking | team | human |
| T19 | Park an unreviewed draft in the review queue | team | code |
| T20 | Reject a record answer that cites a foreign document | team | code |

---

## Client tasks (deck p.8)

### T01
```
task: check the slot is still free
rule_known: yes
input_bounded: yes
cost_of_error: double-booked patient arrives at the counter; front desk notices on arrival
sign_off: none
rung: code
why: Capacity per slot is a number in a row; a unique constraint and a
  transaction answer it exactly. A model adds risk and no value.
eval_cases: w1-booking-retry-001, w1-booking-race-002
```

### T02
```
task: send the booking confirmation
rule_known: yes
input_bounded: yes
cost_of_error: patient misses the appointment, which feeds the 18% no-show rate; noticed only in no-show stats
sign_off: none
rung: code
why: A templated SMS from a confirmed appointment row. The text must be
  deterministic so it never states something the booking does not.
eval_cases: w1-sms-template-001
```

### T03
```
task: convert a PDF to text
rule_known: yes
input_bounded: partly (text PDFs yes; scanned PDFs need OCR)
cost_of_error: a missed page means an answer without its evidence; noticed when a citation is missing
sign_off: none
rung: code
why: A parser with an OCR fallback is deterministic tooling. Its quality is
  measured by character error rate, not judged by a model.
eval_cases: w2-ingest-text-001, w2-ingest-scanned-002
```

### T04
```
task: check whether a medicine is in stock
rule_known: yes
input_bounded: yes
cost_of_error: patient walks to the pharmacy for an out-of-stock drug; pharmacist notices
sign_off: none
rung: code
why: A lookup by composition and clinic behind one interface (CSV now, API
  later). Staleness is shown from the as_of column, never guessed.
eval_cases: w3-stock-fresh-001, w3-stock-stale-002
```

### T05
```
task: map symptoms to a specialty
rule_known: partly (a crosswalk exists, but free text is open-ended)
input_bounded: no
cost_of_error: wrong specialist, wasted visit, a front-desk call; patient and front desk notice
sign_off: none (the patient picks the doctor at the interrupt)
rung: llm
why: Free-text symptoms need a model, but the output is constrained to the
  validated 38-specialty crosswalk. Confidence < 0.6 asks one clarifying question.
eval_cases: w1-001..w1-030
```

### T06
```
task: answer a question on a report
rule_known: no
input_bounded: partly (the patient's own chunks only, filtered in SQL)
cost_of_error: an unfaithful answer misleads the patient; often unnoticed, hence the judge and citations
sign_off: none
rung: llm
why: Generation is the fuzzy step. Retrieval scope, citation resolution and
  the no-diagnosis check are all code around it.
eval_cases: w2-ask-001..w2-ask-030
```

### T07
```
task: turn dictation into a SOAP note
rule_known: no
input_bounded: yes (one transcript)
cost_of_error: an invented line in the clinical record; the doctor carries the liability
sign_off: consulting doctor (via T11)
rung: llm
why: Structuring speech is the model's job. Because T11 is a separate human
  step, the drafting itself is LLM-augmented, not human.
eval_cases: w3-001..w3-040 (Pro)
```

### T08
```
task: decide whether a question needs a doctor
rule_known: partly (doses and diagnoses are known triggers; phrasing varies)
input_bounded: no
cost_of_error: the assistant answers a dosing question; patient harm; the CMO notices
sign_off: none
rung: llm
why: Keyword rules catch the obvious cases first. A classifier catches
  paraphrases. When uncertain, it hands off to a doctor.
eval_cases: w2-redteam-dose-001, w2-redteam-diagnosis-002
```

### T09
```
task: detect red flags (rules first, LLM second)
rule_known: partly (the rule list comes from D2 Q2)
input_bounded: no
cost_of_error: a missed emergency; patient harm; target recall is 100%
sign_off: none (escalation is automatic to the emergency desk)
rung: llm
why: The deterministic rules fire before any model call and cannot be
  overridden. The LLM is only a second net that can add flags, never remove them.
eval_cases: w1-017 (pack p.14 case A), w1-redflag-*
```

### T10
```
task: choose among six candidate doctors
rule_known: partly
input_bounded: yes
cost_of_error: a sub-optimal shortlist; the patient can still choose; low cost
sign_off: patient (at the interrupt)
rung: agentic
why: The path depends on tool results: doctor languages, the next free slot,
  and no-show risk. The model ranks, and the patient decides.
eval_cases: w1-shortlist-001..005
```

### T11
```
task: sign the clinical note
rule_known: n/a
input_bounded: n/a
cost_of_error: legal and clinical liability
sign_off: consulting doctor, every time (RFP addendum)
rung: human
why: The addendum says nothing auto-signs. An unsigned note is a draft by
  schema, and the graph interrupts before persistence.
eval_cases: w3-no-persist-unsigned-001
```

### T12
```
task: pick a substitute medicine
rule_known: partly (same composition)
input_bounded: yes
cost_of_error: patient harm from the wrong substitute; liability on the pharmacist
sign_off: pharmacist
rung: human
why: Pharmacy fears software-made substitutions. The system may list
  same-composition options with stock, and the choice stays with the pharmacist.
eval_cases: w3-substitute-never-auto-001
```

---

## Tasks the team found

### T13
```
task: check caregiver consent before a read
rule_known: yes (grantee, scope, purpose, expiry)
input_bounded: yes
cost_of_error: DPDP breach (a stranger reads a record); compliance notices, or worse, the patient
sign_off: none
rung: code
why: Consent is a row checked on every read. A model must never decide
  access.
eval_cases: w2-consent-expired-001, w2-consent-wrong-scope-002
```

### T14
```
task: de-identify a payload before it leaves the boundary
rule_known: partly (Presidio plus Indian phone and ABHA recognisers)
input_bounded: no
cost_of_error: identified data in a vendor log; regulatory breach; noticed only by the replay test
sign_off: none
rung: code
why: Detection is pattern- and NER-based tooling, not generation. It is proven
  by replaying 200 outbound payloads with zero hits.
eval_cases: platform-egress-replay-200
```

### T15
```
task: delete consultation audio after 24 h
rule_known: yes
input_bounded: yes
cost_of_error: retention breach against the RFP answer; compliance notices
sign_off: none
rung: code
why: An object-store lifecycle rule plus a scheduled job that writes an
  audit event. It is pure arithmetic on timestamps.
eval_cases: platform-audio-delete-001
```

### T16
```
task: detect the language of a patient intake
rule_known: partly
input_bounded: no (mixed Hindi-English, transliterated)
cost_of_error: a wrong specialty match on Hinglish input; patient notices
sign_off: none
rung: llm
why: Code-mixed, romanised Hindi defeats simple detectors. A small model
  tags the language so the matcher gets a normalised intake (D3 row A03).
eval_cases: w1-hinglish-001..010
```

### T17
```
task: extract medicines from a transcript
rule_known: partly (formulary names are known; speech is not)
input_bounded: yes
cost_of_error: an invented molecule enters the plan; the doctor may sign it
sign_off: consulting doctor (via T11)
rung: llm
why: Extraction is fuzzy. A molecule is "supported" only if (i) the transcript
  states it, or (ii) a brand the transcript states maps to exactly ONE
  composition in the cleaned formulary by deterministic lookup. The model never
  supplies a molecule. Each medicine records source = "transcript" |
  "formulary:<product name>". An unresolved molecule, or a class named without
  one, yields "[doctor to specify]" + flag molecule_unresolved; a brand with
  more than one composition yields the same + flag molecule_ambiguous.
  Pack hard case "brand versus generic": run 20261102-220544 showed the model
  echoing the brand as the molecule ("Atorva"), which the old fallback accepted,
  so a brand name stood in for a molecule the doctor never said. Looking the
  brand up in the formulary keeps the useful case (Atorva -> atorvastatin)
  without trusting the model, and refuses it where the formulary is ambiguous.
eval_cases: w3-class-not-molecule-001 (pack p.28); w3 eval set v2, tag brand_generic
```

### T18
```
task: override an assistant-made booking
rule_known: n/a
input_bounded: yes
cost_of_error: the patient is moved without agreement; front desk owns it
sign_off: front desk
rung: human
why: Meena needs a queue she controls. The assistant proposes, and front desk
  can always override. Every override is audited.
eval_cases: w1-override-audited-001
```

### T19
```
task: park an unreviewed draft in the review queue
rule_known: yes (interrupt timeout)
input_bounded: yes
cost_of_error: a lost draft or a failed run; the doctor re-dictates; noticed at the end of the session
sign_off: none
rung: code
why: A timeout on the interrupt moves the thread to a queue with an owner
  and an SLA, instead of failing (pack p.27, D3 row A04).
eval_cases: w3-interrupt-timeout-001
```

### T20
```
task: reject a record answer that cites a foreign document
rule_known: yes
input_bounded: yes
cost_of_error: a scope leak; the target is zero
sign_off: none
rung: code
why: Every cited doc_id must belong to the caller's own retrieved set. This is
  the deterministic second line behind the SQL filter.
eval_cases: w2-redteam-pdf-001, w2-foreign-docid-002
```

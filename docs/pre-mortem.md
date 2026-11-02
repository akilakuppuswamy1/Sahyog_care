# Pre-mortem — written before the first graph node

**Status:** v1 · 2026-11-02 (day 5) · reread on day 12 (deck p.44)
**Rule:** a failure that happens without its signal firing is a finding for the memo, not a secret.

It is two weeks from now and the steering review went badly. These are the reasons, written while they can still be prevented.

| # | Failure | Early signal | Detection we build | Response | Owner |
|---|---|---|---|---|---|
| 1 | A patient sees another patient's chunk | A citation whose doc_id is not in the caller's record | Composite FK chunk→document (built, tested); foreign-doc_id check on every answer; red-team PDF in the W2 eval set | Block the answer, write an incident, review the retrieval filter | Records lead |
| 2 | A medicine is invented in a draft | A molecule in P that is absent from the transcript | Transcript-grounding check on every medicine line; eval case "class without molecule" (pack p.28) | Replace with "[doctor to specify]", log the case | Scribe lead |
| 3 | A red flag is missed | An emergency presentation routed to a booking | Red-flag recall on every eval run (12 cases now, target 100%); rules run before any model; the model can only add flags (ADR-003) | Stop the release, add the case, widen the rule set | Clinical safety lead |
| 4 | A slot is double-booked | Two live appointments on one slot_id | `CHECK (booked <= capacity)` plus idempotent `book_slot()` (built, tested); retry test | Front-desk override; client apology naming the fix | Scheduling lead |
| 5 | Cost runs away | Tokens per triage turn rising week on week | Cost meter per run, daily budget alert | Route the step to a small model; cap turns per session | Platform lead |
| 6 | Doctors ignore the drafts | Review time rising; edits touching every field | Edit distance and review time on the dashboard | Shadow five consultations; fix the template before the model | Product lead |
| 7 | Hinglish intake breaks the matcher | Low confidence clustered on code-mixed input | 4 Hinglish cases in W1 (2 red flag, 2 routine); grow to 10+ at Pro; per-tag accuracy in the eval report | Language-detect node normalises before match (D3 A03) | ML lead |
| 8 | The specialty crosswalk is wrong and nobody reviewed it | Specialty accuracy < 85% concentrated in a few diseases | `reviewed_by` empty is counted in the transform tally (41 unreviewed today) | Clinical review of all 41 rows before day 7; misroutes become eval cases | Clinical safety lead |
| 9 | Identified text reaches the LLM vendor | A scrubber miss on Indian names, phone or ABHA formats | 200-payload egress replay with zero hits (D7); custom recognisers | Egress stops until fixed (ADR-001 trigger c) | Platform lead |
| 10 | The demo fails live: the LLM API is down or slow | Timeouts in traces the day before | Timeouts, circuit breaker and a human-queue fallback (p.36); a recorded demo (p.50) | The run degrades to the front-desk queue; the recording plays | Pod lead |
| 11 | "Works on my machine" | The tests pass in one environment and fail on another | Run the README's exact commands on the pod machine (we already hit: bare pytest, port 5432, SQLSTATE P0002, missing `six`) | Every environment surprise goes into the AI log the same day | Pod lead |

## Already happened (days 1–4)
Row 11 fired early, as intended, and the database guarantees behind rows 1 and 4 were tested against real Postgres:
- **Database guarantees.** The real-Postgres tests showed the immutability trigger raised the wrong SQLSTATE. Fixed, with migration 003.
- **Environment.** Bare `pytest`, port clashes and a missing dependency each broke the first run on the pod machine.
- **Data surprises.** The real transcription file broke two assumptions the fixtures encoded. Both are fixed and covered by regression tests.

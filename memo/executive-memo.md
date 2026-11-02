# Sahyog Care Companion — executive memo
_Pod: <names> · 2 Nov 2026 · Tier: Core · Client figures (41%, 9,000/day, 2.4M, 11 min) are the
client's own, awaiting discovery confirmation; all system figures are from
synthetic data, our eval runs and our API request logs_

**Result.** A Sahyog patient can describe symptoms in English or Hinglish and book a slot
without a phone call, and can ask questions of their own reports and get answers that cite the page they came
from. On our golden sets, all 13 red-flag presentations among 34 triage cases went to emergency help (12 to the
emergency desk, 1 to a crisis line) with 0 false alarms, specialty routing was correct on all 16 booking cases ([20261102-180728](../evals/runs/w1-20261102-180728-graph.md), [20261102-180812](../evals/runs/w1-20261102-180812-graph.md), [20261102-180851](../evals/runs/w1-20261102-180851-graph.md)),
and no answer drew on another patient's record (0 scope leaks across 30 record questions, including a planted
red-team PDF that tries to make the assistant leak). Booking works end to end in the browser demo
(checked by hand) and in API tests;
completion rate and time to book are not yet measured.

**Why it matters.** 41% of Sahyog's 9,000 daily appointments are still booked by phone. The two workflows we
shipped target that number and the 2.4M report PDFs patients cannot currently question. The third mandate —
returning the 11 minutes a doctor spends typing — is the scribe workflow, which is our next tier.

**Decisions we would defend.**
- *Rules before models.* Red-flag rules run before any model call; the model may add a flag, never remove one.
  Rules alone decide every emergency they catch.
- *The patient filter lives in SQL, not the prompt.* Every retrieval is `WHERE patient_id = <from login>`; a
  caregiver sees records only while consent is valid; the assistant's record reads are written to an access
  log the patient can see.
- *Hybrid deployment.* Records, embeddings and the database run on infrastructure we control;
  text passes a scrubber before it reaches the language model, and everything sits behind one adapter, so changing
  model is configuration, not a rewrite — triage and record answers already run on different models that way.
  India-region hosting and full
  de-identification are Architect-tier work.
- *Right-sized models.* A large model for triage, where a smaller one failed our safety checks; a model a fraction
  of the size for record answers, where it met every target we measure.

**What went wrong, and what it taught us.**
- A latency fix we made regressed availability (up to 24% of bookings handed to the front desk); running the eval three times caught it, and the saved run files showed why.
- A slow model turn (11.6 s) plus an automatic retry our coding agent
  added to the demo page made booking fail in
  the browser. Our tests had missed it; the request logs did not. Booking is now idempotent per request key.
- A test passed for the wrong reason, and a safety guard mistook "238 mg/dL" for a medicine dose. Both were caught
  by running against the real database and the eval set, not by reading the code.

**Where we fall short.** Triage p95 was 2.6-4.1 s across our last three runs, under the 4 s target in two of three. Answer faithfulness is not yet
measured: deterministic citation and number checks are our proxies, and a calibrated judge comes next. Cost
per consultation has a formula and measured token counts, but no priced figure yet. All are in the eval report
with the tier that closes them.

**Next.** Show across repeated runs that the new timeout and fallback hold
triage under 4 s; the scribe with doctor sign-off; a priced cost per consultation; a load test at 1,400 requests
an hour.

**If we did it again,** we would write the browser path into the test suite on day one — the failure that
reached a user came through a path our tests did not walk.

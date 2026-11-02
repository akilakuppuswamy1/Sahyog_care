# Sahyog Care Companion — executive memo
_Pod: <names> · 2 Nov 2026 · Tier: Core · All figures from synthetic data and our own eval runs_

**Result.** A Sahyog patient can now describe symptoms in English or Hinglish and leave with a confirmed slot,
without a phone call — and can ask questions of their own reports and get answers that cite the page they came from.
On our golden sets, every emergency presentation was routed to the emergency desk (red-flag recall 100%, 34 cases,
0 false alarms), specialty routing was correct on every case, and no answer ever drew on another patient's record
(0 scope leaks across 30 record questions, including a planted red-team PDF that tries to make the assistant leak).

**Why it matters.** 41% of Sahyog's 9,000 daily appointments are still booked by phone. The two workflows we
shipped target that number and the 2.4M report PDFs patients cannot currently question. The third mandate —
returning the 11 minutes a doctor spends typing — is the scribe workflow, which is our next tier.

**Decisions we would defend.**
- *Rules before models.* Red-flag rules run before any model call; the model may add a flag, never remove one.
  A safety outcome never depends on a prompt.
- *The patient filter lives in SQL, not the prompt.* Every retrieval is `WHERE patient_id = <from login>`; a
  caregiver sees records only while consent is valid; every read is in an access log the patient can see.
- *Hybrid deployment.* Records, embeddings and the database stay in-region; only scrubbed text reaches the language
  model, behind one adapter so the vendor can be swapped in a day.
- *Right-sized models.* A large model for triage, where a smaller one failed our safety checks; a model a fraction
  of the size for record answers, where it met every target.

**What went wrong, and what it taught us.**
- A slow model turn (11.6 s) plus an automatic retry our coding agent added "for robustness" made booking fail in
  the browser. Our tests had missed it; the request logs did not. Booking is now idempotent per request key.
- A test passed for the wrong reason, and a safety guard mistook "238 mg/dL" for a medicine dose. Both were caught
  by running against the real database and the eval set, not by reading the code.

**Where we fall short.** Triage latency varies with the model provider: p95 2.8 s on one run, 9.6 s on another,
against a 4 s target. Answer faithfulness is measured by deterministic checks, not yet by a calibrated judge. Cost
per consultation has a formula and measured token counts, but no priced figure yet. All are in the eval report
with the tier that closes them.

**Next.** A timeout and a fallback model to hold triage under 4 s; the scribe with doctor sign-off; tracing and a
priced cost per consultation; a load test at 1,400 requests an hour.

**If we did it again,** we would write the browser path into the test suite on day one — the one failure that
reached a user came through the only path our tests did not walk.

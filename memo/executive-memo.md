# Sahyog Care Companion — executive memo
_Pod: Akila Kuppuswamy · Tier: Pro (tag `pro-complete`) · All system figures from synthetic data and our saved eval runs; client
figures (41% phone bookings, 9,000 appointments/day, 11 min typing per consultation) are the client's own._

**Result.** Sahyog patients can describe symptoms in English or Hinglish and book the right doctor without a phone
call, and ask questions of their own reports with every answer cited to its page. Doctors can turn a consultation
transcript into a SOAP draft, see each medicine checked against clinic stock, and sign an immutable note. On our
golden sets every emergency presentation went to emergency help (red-flag recall 100% in every run; 1 false alarm in the latest run,
   071432, and none in the three before it), specialty
routing was correct on all 16 booking cases across three runs (180728, 180812, 180851), no record answer drew on
another patient's data (0 scope leaks on 80 questions including the red-team PDF, run 104837), and the scribe
invented no medicine (0 invented molecules on 40 transcripts, run 114035).

**Decisions we would defend.**
- *Rules before models.* Red-flag rules decide emergencies before any model call; a model may add a flag, never remove one.
- *Isolation in the database.* The patient comes from the login and is a SQL filter on every retrieval; caregivers see
  records only while consent is valid; record reads are audited and visible to the patient.
- *Hybrid deployment.* Records, embeddings and the database stay on our infrastructure; every model call passes a
  de-identification step (0 identifiers in a 200-payload replay, p95 14 ms, run egress-051910).
- *Nothing the doctor did not say.* A medicine reaches the note only if the transcript states it or our formulary maps a
  stated brand to one composition; anything else is left as "[doctor to specify]".
- *Measured, not asserted.* Golden sets written before code, results generated from saved runs, and an answer-quality
  judge trusted only after calibration against our own labels on items it was never tuned on (kappa 0.69).

**What went wrong, and what it taught us.** A latency fix (tighter timeouts) quietly sent 6 of 16 expected bookings to the front desk in
   one run (172623); running the eval three times instead of once caught it. Expanding W2 to 80 cases found a diagnosis slipping
through in Hinglish ("confirm karo"); both the input rule and the output guard had missed it, and both now have tests.
The scribe once put a dose ("625 mg") in the medicine field of a multi-ingredient brand; grounding now validates the
field type. Our coding agent added a browser retry "for robustness" that broke booking; request logs, not tests, found it.

**Where we fall short.** Scribe field F1 is 0.82 and medicine precision/recall 0.83 against a 0.9 target; the
remaining medicine misses are placeholders left for the doctor, not wrong drugs. Record-answer faithfulness is 0.87
against 0.9. Triage latency is under 4 s in two of three runs. Doctor review time, booking
   completion and clinic-hours availability in production are not yet measured, there is no speech-to-text, and eval cases still await clinical review.

**Cost.** One triage turn, one record answer and one scribe draft cost about 0.00029 as an illustrative sum of the three unit costs in the cost sheet (provider price currency, list
   prices of 2 Nov 2026); the cost sheet's own consultation scenario covers triage and records only. The scribe is the largest line; the real per-consultation cost needs
the actual visit mix.

**Next.** India-region hosting with least-privilege roles, a load test at 1,400 requests an hour, and a doctor role-play
to measure review time against the 90-second target.

**If we did it again,** we would put the browser path and repeated eval runs into CI from day one: every failure that
reached a user came through a path we ran only once.

# Sahyog Care Companion — executive memo
_Tier reached: Pro · `pro-complete` · Synthetic evidence; clinical review and production validation remain open._

**Delivered.** Patients can describe symptoms in English or Hinglish, book through the scheduling tools,
and question their own reports with citations. Doctors can turn text transcripts into editable SOAP drafts,
check grounded medicines against clinic stock, and sign an immutable note. Durable ingest runs in a separate
queue worker; shared egress de-identification, model resilience, Postgres tracing and Ops are built.
Red-flag rules precede model calls; patient scope comes from authentication and is enforced in retrieval SQL.

**Measured results.** W3 recorded 0 invented molecules, field F1 0.823, medicine precision/recall
0.833/0.833, and correct stock flags on 25/30 comparisons; draft p95 was
6.583 s ([run 20261103-114035](../evals/runs/w3-20261103-114035.json)). These are synthetic outcomes,
not proof of clinical safety; field and medicine scores remain below the [0.9 target](../evals/report.md).
The offline egress replay found 0 hits in 200 synthetic payloads, with de-identification p95
14.296 ms ([egress-20261103-051910](../evals/runs/egress-20261103-051910.json)); unfamiliar names
and contextual quasi-identifiers remain risks.

W2's 80-case suite recorded `red_team_pass=true` and 0 scope leaks; this is the suite-level
red-team pass, not a claim that every case is adversarial. Faithfulness was 0.868
([run 20261103-104837](../evals/runs/w2-20261103-104837.json)), below the [0.9 target](../evals/report.md).
The matching held-out calibration has kappa 0.694
([20261103-061312-389012](../evals/runs/judge-calibration-20261103-061312-389012.json)).
The [human judge-zero review](../evals/runs/judge-review-w2-20261103-094651.md) of the earlier run found
an actual contradictory absence claim alongside judge errors. It motivated verifier and citation-footer fixes;
it does not re-score the latest run or erase its failures.

**Cost including scribe.** Using the unit costs in the [cost sheet](../docs/cost-sheet.md), an illustrative
consultation comprises a triage turn, record answer and scribe draft: 0.00006828 + 0.00009533 + 0.00012143 = 0.00028504,
in the worksheet's provider price currency (runs 20261103-071432, 20261103-104837 and 20261103-114035).
This sum includes the scribe line; actual consultation cost is unmeasured because the workflow mix is not linked.
Prices are worksheet inputs, not independently verified here; local compute/storage and unbuilt speech-to-text
are excluded. Record-answer cost is an amortized run average.

**Remaining work.** No audio or speech-to-text is built. Doctor review time, booking completion/time,
clinic-hours availability and recovery remain unmeasured. Architect deployment controls are unbuilt:
verified India hosting, encryption, least-privilege roles, distributed limits and retention/recovery validation.
Production identity/OTP, SMS delivery and scheduling the timeout/purge commands also remain open.
Pro denotes delivered scope with these explicit gaps, not production readiness.

**Claim check.** Rechecked against the saved runs, cost sheet, graph/API/worker implementations and D7.
Unsupported claims removed: client business figures, manual browser success, universal privacy/clinical safety,
all quality targets met, production residency, measured consultation savings and doctor time saved.
None is asserted here; the synthetic results cannot establish those outcomes.

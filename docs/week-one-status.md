Posted to the client thread: https://github.com/akilakuppuswamy1/Sahyog_care/discussions/1#discussioncomment-19371922. Filed late on 3 Nov 2026; it records W1 at the end of week one.

# Week-one status — W1 triage

## What runs

Red-flag rules run before any model call. A rules hit goes to the emergency desk with no specialty or slot. Otherwise the flow is specialty → shortlist → interrupt for the patient's pick (LangGraph, Postgres checkpointer) → one-transaction booking, with idempotent replay.

## Measured (synthetic, saved runs)

- Red-flag recall: 100% in every saved run.
- Specialty routing: 16/16 booking cases in w1-20261102-180728, w1-20261102-180812 and w1-20261102-180851.
- p95 latency: 2.6–4.1 s (2.88 s, 2.56 s and 4.09 s respectively), under the 4 s target in 2 of 3 runs. Run w1-20261102-180851 missed it at 4.09 s.

## Traces

Spans are stored in Postgres with the node name and timing, and no patient data.

## Data

Identified data stayed inside the hospital boundary; only scrubbed text went to the model provider.

## Open

D2 questions are awaiting answers. W2 and W3 are next.

# Cost worksheet (Pro, deck p.34)

Generated from the latest saved graph-evaluation JSON files. No eval or model call is run.

Price list date: to fill

Prices are read only from `PRICE_IN_PER_MTOK_W1`, `PRICE_OUT_PER_MTOK_W1`, `PRICE_IN_PER_MTOK_W2`, and `PRICE_OUT_PER_MTOK_W2`; figures are in the provider's price currency.
Unset prices remain formulas marked **to fill**; no price is embedded in this worksheet.

| Line | Measured source and run id | Model | Calls | Measured input / output tokens | Unit count | Cost formula / measured unit cost |
|---|---|---|---:|---:|---:|---|
| Triage | [evals/runs/w1-20261102-180851-graph.json](../evals/runs/w1-20261102-180851-graph.json) (20261102-180851) | qwen/qwen3-235b-a22b-2507 | 45 | 15424 / 1134 | 34 triage turns | (34 triage turns × ((453.65 input tokens × PRICE_IN_PER_MTOK_W1) + (33.35 output tokens × PRICE_OUT_PER_MTOK_W1)) / 1,000,000) / 34 triage turns = to fill |
| Record answers | [evals/runs/w2-20261102-172731.json](../evals/runs/w2-20261102-172731.json) (20261102-172731) | qwen/qwen3-30b-a3b-instruct-2507 | 23 | 10389 / 1222 | 17 record answers | (17 record answers × ((611.12 input tokens × PRICE_IN_PER_MTOK_W2) + (71.88 output tokens × PRICE_OUT_PER_MTOK_W2)) / 1,000,000) / 17 record answers = to fill |
| Scribe | W3 | not built yet | — | not measured | — | not built yet |
| Speech-to-text | — | not built | — | not measured | — | not built |
| Embeddings | Local BAAI/bge-m3 | local model | — | not applicable | — | no API cost; local compute/storage excluded |

## Cost per consultation

Formula: **cost per consultation = total cost / consultations**. Consultation count and the W1/W2 call mix are not linked by the current eval runs, so an actual per-consultation figure is **to fill**.
For a clearly labelled scenario only, assume one measured W1 triage turn and one measured W2 record answer per modeled consultation:
- Total scenario cost = W1 triage unit cost + W2 record-answer unit cost; modeled consultations = 1; cost per consultation = total / 1 = to fill.
- Model routing: W2 uses `qwen/qwen3-30b-a3b-instruct-2507` (run 20261102-172731); W1 uses `qwen/qwen3-235b-a22b-2507` (run 20261102-180851). W2 is routed to the smaller model.

Token totals are run-level measured values divided by the recorded evaluation denominator; they are not isolated per-call token measurements. W2 totals may include model calls for multiple purposes, so its per-answer figure is an amortized run average.

# Cost worksheet (Pro, deck p.34)

Generated from the latest saved graph-evaluation JSON files. No eval or model call is run.

Price list date: 2026-11-02

Prices are read only from `PRICE_IN_PER_MTOK_W1`, `PRICE_OUT_PER_MTOK_W1`, `PRICE_IN_PER_MTOK_W2`, `PRICE_OUT_PER_MTOK_W2`, `PRICE_IN_PER_MTOK_W3`, and `PRICE_OUT_PER_MTOK_W3`; figures are in the provider's price currency.
Unset prices remain formulas marked **to fill**; no price is embedded in this worksheet.

| Line | Measured source and run id | Model | Calls | Measured input / output tokens | Unit count | Cost formula / measured unit cost |
|---|---|---|---:|---:|---:|---|
| Triage | [evals/runs/w1-20261103-071432-graph.json](../evals/runs/w1-20261103-071432-graph.json) (20261103-071432) | qwen/qwen3-235b-a22b-2507 | 90 | 27170 / 2073 | 80 triage turns | (80 triage turns × ((339.62 input tokens × PRICE_IN_PER_MTOK_W1) + (25.91 output tokens × PRICE_OUT_PER_MTOK_W1)) / 1,000,000) / 80 triage turns = 0.00006828 |
| Record answers | [evals/runs/w2-20261103-072003.json](../evals/runs/w2-20261103-072003.json) (20261103-072003) | qwen/qwen3-30b-a3b-instruct-2507 | 50 | 23662 / 2799 | 37 record answers | (37 record answers × ((639.51 input tokens × PRICE_IN_PER_MTOK_W2) + (75.65 output tokens × PRICE_OUT_PER_MTOK_W2)) / 1,000,000) / 37 record answers = 0.00008665 |
| Scribe | [evals/runs/w3-20261103-072341.json](../evals/runs/w3-20261103-072341.json) (20261103-072341) | qwen/qwen3-235b-a22b-2507 | 58 | 22227 / 3372 | 40 scribe drafts | (40 scribe drafts × ((555.67 input tokens × PRICE_IN_PER_MTOK_W3) + (84.30 output tokens × PRICE_OUT_PER_MTOK_W3)) / 1,000,000) / 40 scribe drafts = 0.00014523 |
| Speech-to-text | — | not built | — | not measured | — | not built |
| Embeddings | Local BAAI/bge-m3 | local model | — | not applicable | — | no API cost; local compute/storage excluded |

## Cost per consultation

Formula: **cost per consultation = total cost / consultations**. Consultation count and the W1/W2 call mix are not linked by the current eval runs, so an actual per-consultation figure is **to fill**.
For a clearly labelled scenario only, assume one measured W1 triage turn and one measured W2 record answer per modeled consultation:
- Total scenario cost = W1 unit cost + W2 unit cost = 0.00015492; modeled consultations = 1; scenario cost per consultation = total / 1 = 0.00015492.
- Model routing: W2 uses `qwen/qwen3-30b-a3b-instruct-2507` (run 20261103-072003); W1 uses `qwen/qwen3-235b-a22b-2507` (run 20261103-071432). W2 is routed to the smaller model.
- Scribe model: `qwen/qwen3-235b-a22b-2507` (run 20261103-072341); W3 cost is measured per scribe draft.

Token totals are run-level measured values divided by the recorded evaluation denominator; they are not isolated per-call token measurements. W2 totals may include model calls for multiple purposes, so its per-answer figure is an amortized run average.

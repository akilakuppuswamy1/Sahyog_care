# D5 — Is there enough data? Sizing, redone with our own counts

**Status:** v0.1 · 2026-11-02 · owner: data lead
**Acceptance (deck p.22):** the inventory exists, and these three checks pass:
- every eval case has at least 5 candidate chunks
- every specialty has at least 10 doctors
- every clinic has slots on every working day

## The three checks

| Check | Status | Evidence |
|---|---|---|
| Every specialty has ≥ 10 doctors | **Pass** | `test_every_specialty_has_at_least_10_doctors`; a guard in `002_load_generated.sql` |
| Every clinic has slots on every working day | **Pass** | `test_slot_calendar_480k_and_every_clinic_every_day` (28 clinics × 60 days); the same guard in SQL |
| Every eval case has ≥ 5 candidate chunks | _Pending_ | Needs the 2,000 report PDFs and the W2 eval set (day 5). The check runs as a query over `doc_chunk` per eval case. |

## Sizing: pack estimate vs measured

| Store | Pack estimate (p.22) | Ours | Basis |
|---|---|---|---|
| Slot calendar | 480,000 rows, ~38 MB | 480,000 rows; **90 MB** table + indexes; 33.6 MB as CSV | Measured on Postgres 16 after load |
| Doctors | 500 | 500, 0 rejected | Measured |
| Vector index | 60,000 chunks × 1024 × 4 B ≈ 246 MB | Same arithmetic; not yet measured | 2,000 PDFs × ~30 chunks. Add HNSW index overhead, roughly 1.5–2×, so plan for ~400–500 MB. |
| Chunk text | ~72 MB | Not yet measured | 60,000 × 1,200 characters |
| Checkpoints | "Tens of MB a day" | **~150–300 MB a day; 4.5–9 GB at 30-day retention** | 9,000 runs × 8 steps × 2–4 KB. The pack's figure looks low by about 10×; re-measure from real checkpoints on day 7. |
| Audio | ~70 GB a day, deleted in 24 h | 72 GB a day peak on disk | 9,000 × 8 min × 1 MB/min. The lifecycle rule must run hourly, not daily, to stay within one day of storage. |

## Findings for the architecture pack
1. **The slot table is about 2.4× the pack's estimate once indexed.** It is still trivial, but the estimate counted rows only.
2. **Checkpoint storage is under-estimated.** The 30-day retention rule (deck p.31) matters more than the pack suggests, so a purge job goes on the day-7 task list.
3. **Doctor count conflict.** p.19 audits against a "1,400-doctor split" but p.20 generates 500. We use 500 (500 × 60 × 16 = 480,000 matches p.20 and p.22) and record this conflict in the inventory.

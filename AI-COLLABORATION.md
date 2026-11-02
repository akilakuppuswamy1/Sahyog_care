# AI-collaboration log

**This is a pass/fail gate (deck p.49).** Each pull request needs one entry that says what the agent proposed, what the team rejected, and why.

## Entry template
```
### PR #<n> — <title> — <date>
agent: <tool and model>
proposed: <what the agent produced>
accepted: <what we kept>
rejected: <what we threw out>
why: <the reason, with the test or check that caught it>
```

---

### PR #1 — Repo scaffold and discovery drafts (D1–D3, inventory) — 2026-11-02
agent: Claude (Opus 5.5), chat session
proposed:
- The p.48 repo layout
- 20 task cards (12 client and 8 team-found)
- 10 discovery questions
- 15 assumption rows
- An inventory skeleton and a row-count script
accepted: _fill in after the pod reviews_
rejected: _fill in, e.g. any question the pod rewrote or any rung the pod moved_
why: _fill in_
notes:
- The agent deliberately left exact row counts and most licences as TBD rather than guessing them (house rule: exact counts before use).
- The agent flagged that the No Shows Kaggle slug is unconfirmed.

### PR #2 — Data estate: schema, generated layer, transforms, D4, D5 — 2026-11-02
agent: Claude (Opus 5.5), chat session
proposed:
- A Postgres schema for the 14 tables with booking, consent, note-immutability and audit rules enforced in the database
- Seeded doctor and slot generators
- Five Kaggle transforms with tallies
- 34 Python tests plus 10 database scenarios
- 14 data contracts and the D5 sizing
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes (things the agent got wrong or changed, for the steering review question "where did your agent mislead you?"):
- **Bug caught by a test:** the first `parse_price` stripped every non-digit except '.', so "Rs. 12" became 0.12. `test_price_parsing` failed; the fix strips a currency prefix and then requires a clean number.
- **Deliberate deviation from the pack:** p.20 has an LLM write the doctor directory. The agent proposed seeded code instead (reproducible, free, bias-auditable). The pod should accept or reject this explicitly.
- **Pack estimate corrected:** the slot table measures 90 MB with indexes against the pack's 38 MB, and checkpoint storage is estimated at about 10× the pack's figure (docs/d5-sizing.md).
- **Not verified in the build sandbox:** the pytest database tests (psycopg could not be installed). The same scenarios were run through psql and all passed. Run `pytest` against Docker locally to confirm.
- **Crosswalks** (disease→specialty, transcription label→specialty) were drafted by the agent and are unreviewed. The reviewed_by column is empty on purpose, because deck p.19 requires a human reviewer mark on every row.
- **Environment slip found on the pod's machine:** the agent ran tests with `python -m pytest` (which puts the repo root on sys.path), so it missed that a bare `pytest` fails with "No module named 'data'". Fixed by adding `pythonpath = .` to pytest.ini. Lesson: run the exact command the README gives, on the target OS.
- **Port clash on the pod's machine:** host port 5432 was held by other local services, so the DB tests reached the wrong Postgres ("password authentication failed"). The container now maps a configurable host port, `${DB_PORT:-55432}`.
- **Wrong SQLSTATE caught by the DB tests:** the agent tagged the immutability trigger with ERRCODE 'P0002', which is Postgres's code for no_data_found, so drivers reported a refused write as "no data". The rows were still protected; only the error class was wrong. Changed to P0001 (raise_exception) in 001_schema.sql, plus migration 003 for the existing database. This was only visible when the tests ran against real Postgres on the pod's machine.
- **Two transform bugs found only on the real Kaggle file** (fixtures did not reproduce them):
  (1) dedupe kept the first label, so 976 notes landed under the generic "Surgery"; it now keeps the most specific label and records all of them (specialty-less notes fell from 551 to 54);
  (2) ".," is a line separator in mtsamples even with no following space ("clear.,Neck:"); the first regex missed 1,795 notes; now 0.
  Each has a regression test. Lesson: fixtures encode what the agent *thinks* the mess is, so always run on the real file before trusting a transform.

### PR #3 — Day 5: architecture pack v1, ADRs 1-3, W1 eval set, pre-mortem — 2026-11-02
agent: Claude (Opus 5.5), chat session
proposed:
- docs/architecture.md: context view, container view, W1 graph (Mermaid), and 10 corrections to the pack's diagrams
- ADR-001 hybrid deployment; ADR-002 safety rules in the database; ADR-003 W1 graph shape
- evals/sets/w1_triage.jsonl: 30 cases (12 red flag, 14 routine, 2 low-confidence, 2 two-specialty; 4 Hinglish; pack cases A and B)
- evals/tests/test_eval_sets.py: the eval set is itself tested
- docs/pre-mortem.md with 11 failure modes
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- Day-5 gate held: evals exist, graph code does not.
- The eval cases and red-flag routing were drafted by the agent, not a clinician. added_by says "clinical review pending" on purpose.
- The agent's first pre-mortem draft claimed 7 Hinglish cases. The eval file has 4. Corrected before commit, by counting the file instead of trusting the summary.
- W2 eval cases (30) are deferred to before W2 code (day 8), because they need the generated report PDFs and patient records.

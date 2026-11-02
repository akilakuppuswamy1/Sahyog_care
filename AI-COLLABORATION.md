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
### PR #4 — W1 graph (days 6-7) — 2026-11-02
agent: Claude (Opus 5.5), chat session (no access to the pod machine; delivered as a zip)
proposed:
- Red-flag rules in code with English + Hinglish patterns; the model net may only add flags
- Egress guard (regex scrub, refuse-to-send) and an output guard against diagnosis or dose wording
- OpenRouter client behind one interface (vendor swap = one file)
- W1 nodes as plain functions, wired into a LangGraph graph with two interrupts (clarify, pick)
- Postgres and in-memory tools; a CLI demo; the eval runner
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- **Overfitting caught:** the first rule set scored 30/30 on the eval set, then missed 3 of 12 held-out paraphrases
  ("tightness in my chest", "cannot breathe", "face suddenly drooped"). Fixed; the held-out set is now its own test.
- **Framework trap avoided:** LangGraph injects its own object into node parameters named `store`, so the tools
  parameter was renamed `tools`. Worth confirming on the installed version.
- **Not run by the agent:** the LangGraph graph tests and the OpenRouter eval. PyPI was blocked in the agent's
  sandbox, so only the framework-free logic (rules, guards, nodes, scoring) was tested there. First run on the pod machine is the real test.
- **Withheld from the reply:** the pack's "a nurse will call within two minutes" (p.14), until D2 Q1 / D3 A09 confirm staffing.
- OpenRouter model chosen: qwen/qwen3-235b-a22b-2507 (2026-11-02).
- **Negation safety regression:** negation could bleed into later clauses and suppress an emergency flag. The held-out tests now include both "not feeling well crushing chest pain ..." and "Not feeling well. Chest pain with sweating ..." and assert a red flag.
- **Model trial:** qwen3-30b had clarify accuracy 0.0 and two red-flag false alarms in the graph run; qwen3-235b was retained.
- **Latency miss reported:** p95 fell from 38.53 s to 9.63 s after parallel model checks, a token cap, and provider sorting, but remained above the 4 s target.
### PR #5 — W2 My records (days 8-9) — 2026-11-02
agent: Claude (Opus 5.5), chat session (no access to the pod machine; delivered as a zip)
proposed:
- A report generator: 10 fixed fixture reports (5 patients, one red-team note) plus seeded bulk patients
- Ingest: pypdf parse, section chunking, the identity block dropped, injection and PHI flags, local bge-m3 embeddings
  cached by content hash, status queued -> parsing -> indexed | failed, an audit event per write
- Migration 004: a tsvector column ('simple' config) + GIN index, an HNSW index, chunk flags
- Hybrid retrieval (pgvector + full-text + abbreviation/Hinglish expansion, RRF), the patient filter in SQL on every query
- The ask graph: consent and scope guard -> doctor hand-off rules -> retrieve -> grade/widen -> cited answer ->
  deterministic verifier (citations resolve, numbers grounded, no diagnosis wording) with a verbatim fallback
- The W2 eval set (30) and evals/run_w2.py; 32 unit tests + a pgvector red-team test
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- **Design choice:** report text comes from seeded templates, not an LLM, so eval values are exact (deck p.20 says LLM).
- **Design choice:** identifiers live only in the PATIENT DETAILS block, which the chunker drops, so no name, phone or id
  reaches a chunk, an embedding or a prompt. A unit test asserts it on every fixture chunk.
- **Red team:** the injected note is flagged at ingest and excluded from retrieval in SQL; the DB test also proves that
  the same query WITHOUT the patient filter reaches other patients (so the filter is what protects).
- **A test caught a wrong assumption by the agent:** it assumed excerpt E1 would hold the CBC values, but section
  chunking puts REMARKS in its own chunk, which ranked first. The test was fixed to cite both, as a real answer would.
- **Not run by the agent:** pgvector, bge-m3, LangGraph and OpenRouter (no PyPI or Postgres extension in its sandbox).
  The first real run of the W2 graph and the DB tests is on the pod machine.
- **To tune on the eval set:** V_MIN = 0.45 (the "relevant context" threshold) and the abbreviation map.
- **First pod eval (110626):** route accuracy 1.0 and zero scope leaks, but content_hit was 0.824. Cases w2-002 and w2-006 got bare yes/no replies; w2-011's fallback showed only the final line.
- **False confidence in the blood-pressure test:** a fake model error made the unit test pass without proving the relevance gate. On Postgres, any keyword hit (including "COMPLETE BLOOD COUNT") counted as relevant. The gate now scores topic coverage, and the graph test uses a scripted model that answers.
- **Verifier miss:** the 122732 run had verified_rate 0.824 because the dose pattern matched lab units such as "238 mg/dL"; a lookahead now excludes mg/dL and regression cases cover lab units. The guard's missed "this could be hepatitis" wording was also added to its diagnosis tests.
- **Final W2 run (150927):** route accuracy, content_hit, and verified_rate were all 1.0, with zero scope leaks and p95 latency 2.15 s. W2 uses qwen3-30b via `OPENROUTER_MODEL_W2`; the run artifacts identify local BAAI/bge-m3 embeddings.
- **Embedding-check design error:** the agent first compared question-to-question vectors, which did not test retrieval. The pod redid the check as question-to-document; all phrasings, including Hinglish, ranked the correct report first.
- **Partial patch extraction:** an incompletely extracted zip left `nodes.py` new and `retrieve.py` old, causing an ImportError on pytest. Afterward, Codex applied changes from written prompts instead of zips and verified them with the full test run.
- **Environment/reproducibility incident:** Docker Desktop failed with HTTP 500 and was reinstalled per-user with WSL 2. The database volume was lost and rebuilt from migrations 001-004 plus fixture ingest, confirming the schema could be reconstructed from the repository.

### PR #6 — API: W1/W2 service and deployment — 2026-11-02 (ab73a52)
agent: Coding agent (tool/model not recorded in the commit)
proposed:
- FastAPI endpoints and a browser demo for W1/W2
- Idempotency keys, rate limits, JSON request logs, a Docker API service, and a deployment ADR
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- The launch instructions include `python -m uvicorn services.api.main:app --port 8000`.

### PR #7 — Core E2/E3: front desk and PDF upload — 2026-11-02 (d6a90a8)
agent: Coding agent (tool/model not recorded in the commit)
proposed:
- Front-desk queue and booking overrides
- PDF upload with ingest status, records-question redirect, and IST display times
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- The commit adds front-desk and upload routes, UI, database migration, and PostgreSQL upload tests.

### PR #8 — W1 retry incident fix — 2026-11-02 (07d0b8a)
agent: Coding agent (Codex investigated the incident; model not recorded)
proposed:
- Reserve a durable W1 thread ID per (user, idempotency key) and replay the current thread/checkpoint for duplicate requests.
- Remove automatic browser retries; poll known thread IDs and cover concurrent replays in tests.
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- **Incident:** an automatic demo retry replayed an 11.6 s W1 triage POST; the replay returned `thread_id: null`, then a slot click got 409 "Thread is not paused". JSON request logs exposed it; tests did not retry. Codex's first pool-row-factory diagnosis did not reproduce it.
- The commit adds durable idempotency, polling support, and concurrent replay tests. Environment slips: bare `uvicorn` used the global Python without `psycopg_pool`, so the documented command uses `python -m uvicorn`; production dependencies load `.env`, while test dependencies do not.
Steering-review answer: the agent misled us by adding an automatic client retry; request logs exposed the replay and tests initially missed it.

### PR #9 — Agent house rules and collaboration log catch-up — 2026-11-02
agent: Copilot SDK assistant (model not recorded)
proposed:
- Add repository-wide coding-agent rules in `AGENTS.md` and the `CLAUDE.md` pointer.
- Catch up the existing W1/W2 entries and add one entry for each unlogged API commit group, including the incident fix.
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- Documentation-only changes; no code, tests, or data were changed. Unrelated untracked worktree files were left untouched. No `.env` was read.

### PR #10 — W2 pod findings added to collaboration log — 2026-11-02
agent: Copilot SDK assistant (model not recorded); Codex (GPT-6) for architecture v2 and D7
proposed:
- Add the pod's embedding-check correction, partial-zip ImportError, and Docker/DB rebuild evidence to the existing W2 entry.
- Architecture pack v2: context, containers, SQL-derived entity model, compiled W1/W2 exports, W3 Pro design and two sequences; code-verified corrections and ADR/D6/D7 links.
- Offline `tools/export_graphs.py` and subprocess regression `services/agent/tests/test_export_graphs.py::test_export_compiled_graphs`.
- D7 STRIDE controls linked to existing tests, explicit missing-control tiers, and a read-only local DB role inventory/access-review target.
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- Documentation-only update based on pod findings supplied in chat; no code, tests, or data changed. `.env` was not read.
- Architecture/D7 extension: docs, one exporter script and its requested test only; service implementation unchanged. No `.env` read/print, OpenRouter call, eval run or Docker build. The full suite reads eval fixtures as tests but does not execute eval runners.
- Guidance timing: `AGENTS.md` was absent at the initial workspace/ancestor search and appeared during this task; it was read when discovered. This open entry was updated rather than adding another PR entry. Human accepted/rejected/why fields remain untouched.
- What the agent got wrong: first execution used the workspace venv inside the sandbox, which denied access to its base Python interpreter; the tool error exposed this and the offline exporter/test commands succeeded after execution approval. A first metadata-query command lost quotes through Windows native argument handling; its SyntaxError was corrected by piping a Python source block to stdin. Neither failed attempt read application data or `.env`.
- Verification corrected prospective claims: SMS is an audit intent, not a delivery outbox; W2 is not wired to a Postgres checkpointer; note signer equality is not authentication; regex egress is not full de-identification. No production claims were made for Presidio, re-identification, 200-payload replay, OTP, storage encryption, least-privilege roles or India hosting. The deck itself was unavailable for independent artwork/page verification; pack comparisons use the task and existing repo records.
- Read-only local role inventory (55432): only non-system role `sahyog`, with LOGIN/SUPERUSER/CREATEROLE/CREATEDB. Full `python -m pytest` with the specified 55432 DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **240 passed, 13 warnings in 30.14s** (0 failed, no skips; warnings are dependency deprecations).

### PR #11 — Core part 2: evidence report, RFP and one-command startup — 2026-11-02
agent: Codex (GPT-6)
proposed:
- Offline `tools/build_eval_report.py`, generated `evals/report.md`, and a two-fake-run regression covering latest-run selection, history, measured token averages and optional prices.
- Seven-question `docs/rfp-response.md`, with implementation paths, bounded proof and explicit Core/Pro/Architect gaps.
- Shared Python 3.14 API/migrate image, migration ledger with transactional COPY adaptation and startup dependency; missing doctor/slot/report generation, fixture ingest, and patient/consent restart recovery without extending consent expiry.
- Separate cached torch/pip build layers, README Core tier and three-step Start, and evidence-based discovery checkboxes; D2 and incomplete inventory licences remain unticked.
- Isolated PostgreSQL regression for COPY, idempotence, rollback, partial fixture cohorts and unchanged consent expiry.
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- What the agent got wrong: initially used `python`, absent from PATH; shell output caught it. The venv base interpreter then hit sandbox access denial; execution approval allowed the offline generator and tests. An interrupted turn lost the first test session, so the full suite was rerun rather than claiming a missing result.
- Review caught two incorrect evidence paths (`guards.py` and `redflags.py` live under `services/agent/w1/`) and a first-patient-only bootstrap check that missed partial cohorts. Paths were corrected; the bootstrap now inserts missing patients and preserves existing consent expiry, covered by `infra/db/tests/test_migrate.py::test_migrations_copy_rollback_and_restart`.
- No `.env` was read, printed or edited; no eval runner, OpenRouter call or Docker build ran. Compose config passed with `--env-file NUL`, `COMPOSE_DISABLE_ENV_FILE=1`, `--no-env-resolution`, `--no-interpolate`, and `--quiet` to avoid dotenv access. Container startup itself was not executed, as requested.
- Saved runs supply every headline and token number; calibrated faithfulness, end-to-end booking, W3 timing, availability, restore, full residency/retention and exhaustive audit/crash proof remain explicit gaps. Page 16/34 requirements use the supplied task; the source deck was not independently available. Pip caching does not guarantee byte-range resume of every interrupted download.
- Verification: full `python -m pytest` with `DATABASE_URL=postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog` and `PYTHON_DOTENV_DISABLED=1`: **242 passed, 13 warnings in 48.99s**; no failures or skips. Warnings are dependency deprecations. Compose config validation passed (exit 0). Human review fields intentionally unfilled.

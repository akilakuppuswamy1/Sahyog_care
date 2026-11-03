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
accepted: repo layout, 20 task cards, 15 assumptions, inventory script.
rejected: guessed row counts and licences — kept TBD until the real Kaggle files were counted.
why: house rule "exact counts before use"; the real count changed a pack figure (no-show 20.2%, not 18%).
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
accepted: DB-enforced safety rules (ADR-002), seeded doctor/slot generators instead of an LLM (pack deviation), the five transforms.
rejected: first parse_price, keep-first transcription dedupe, ERRCODE P0002, default host port 5432.
why: unit tests, the real Kaggle file and tests against real Postgres on our machine caught each one.
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
accepted: ADRs 1-3, the W1 eval set written before any graph code, the pre-mortem.
rejected: the pre-mortem's claim of 7 Hinglish cases.
why: we counted the eval file — it has 4.
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
accepted: rules-first red-flag gate, add-only model net, qwen3-235b for W1.
rejected: the first rule set (overfit), the negation regex that crossed clauses, qwen3-30b for W1, "a nurse will call within two minutes" wording.
why: held-out paraphrase tests; the safety test "not feeling well … crushing chest pain"; graph eval (clarify 0.0, 2 false alarms); staffing unconfirmed (D2).
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
accepted: hybrid retrieval with the patient filter in SQL, local bge-m3, coverage grade, prompt v2, verifier with mg/dL lookahead.
rejected: "any keyword hit is relevant", the blood-pressure test that passed for the wrong reason, the dose regex matching lab units, zip delivery, the question-vs-question embedding check.
why: real-Postgres graph test; eval runs 110626 → 122732 → 150927; ImportError from a partial zip.
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
accepted: FastAPI + demo page, idempotency keys, rate limits, JSON logs, Docker service, deployment ADR.
rejected: bare `uvicorn` in the README; production deps not loading .env.
why: both failed on first start on our machine.
notes:
- The launch instructions include `python -m uvicorn services.api.main:app --port 8000`.

### PR #7 — Core E2/E3: front desk and PDF upload — 2026-11-02 (d6a90a8)
agent: Coding agent (tool/model not recorded in the commit)
proposed:
- Front-desk queue and booking overrides
- PDF upload with ingest status, records-question redirect, and IST display times
accepted: front desk queue and overrides, PDF upload with status, records-question redirect, IST times.
rejected: nothing wholesale.
why: our browser test showed "What is my HbA1c?" being booked with a doctor, which led to the redirect and 4 new eval cases.
notes:
- The commit adds front-desk and upload routes, UI, database migration, and PostgreSQL upload tests.

### PR #8 — W1 retry incident fix — 2026-11-02 (07d0b8a)
agent: Coding agent (Codex investigated the incident; model not recorded)
proposed:
- Reserve a durable W1 thread ID per (user, idempotency key) and replay the current thread/checkpoint for duplicate requests.
- Remove automatic browser retries; poll known thread IDs and cover concurrent replays in tests.
accepted: idempotent triage per (user, key), no client auto-retry, polling.
rejected: the agent's automatic client retry, and its first diagnosis (pool row factory).
why: our JSON request logs showed a replay returning thread_id null; the tests never retried.
notes:
- **Incident:** an automatic demo retry replayed an 11.6 s W1 triage POST; the replay returned `thread_id: null`, then a slot click got 409 "Thread is not paused". JSON request logs exposed it; tests did not retry. Codex's first pool-row-factory diagnosis did not reproduce it.
- The commit adds durable idempotency, polling support, and concurrent replay tests. Environment slips: bare `uvicorn` used the global Python without `psycopg_pool`, so the documented command uses `python -m uvicorn`; production dependencies load `.env`, while test dependencies do not.
Steering-review answer: the agent misled us by adding an automatic client retry; request logs exposed the replay and tests initially missed it.

### PR #9 — Agent house rules and collaboration log catch-up — 2026-11-02
agent: Copilot SDK assistant (model not recorded)
proposed:
- Add repository-wide coding-agent rules in `AGENTS.md` and the `CLAUDE.md` pointer.
- Catch up the existing W1/W2 entries and add one entry for each unlogged API commit group, including the incident fix.
accepted: AGENTS.md house rules shared by every coding agent.
rejected: none.
why: rules in one file stopped agents reading .env, skipping tests or running evals unasked.
notes:
- Documentation-only changes; no code, tests, or data were changed. Unrelated untracked worktree files were left untouched. No `.env` was read.

### PR #10 — W2 pod findings added to collaboration log — 2026-11-02
agent: Copilot SDK assistant (model not recorded); Codex (GPT-6) for architecture v2 and D7
proposed:
- Add the pod's embedding-check correction, partial-zip ImportError, and Docker/DB rebuild evidence to the existing W2 entry.
- Architecture pack v2: context, containers, SQL-derived entity model, compiled W1/W2 exports, W3 Pro design and two sequences; code-verified corrections and ADR/D6/D7 links.
- Offline `tools/export_graphs.py` and subprocess regression `services/agent/tests/test_export_graphs.py::test_export_compiled_graphs`.
- D7 STRIDE controls linked to existing tests, explicit missing-control tiers, and a read-only local DB role inventory/access-review target.
accepted: architecture v2 generated from the compiled graphs and the SQL, D7 threat model.
rejected: claims we could not prove in code (SMS delivery, W2 checkpointing, authenticated signing, India hosting).
why: each claim was checked against the repository before it went into a document.
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
- Day-8 change-request preparation (docs only): a timed two-hour checklist, ADR-004 draft template in ADR-001's format, and two short client message templates with confirmation, tradeoffs and revised dates.
- Executive memo: save the user-supplied text verbatim in `memo/executive-memo.md`, preserving `<names>`, and audit every factual claim against saved run JSON, the eval report, API controls, implementation and tests; report caveats separately without revising the memo.
accepted: eval report generated from saved runs, RFP response, migrate service, change-request kit, memo.
rejected: memo wording that overstated results ("every read", "vendor swap in a day", a mixed-denominator percentage).
why: a claim-by-claim audit of the memo against run JSON and tests.
notes:
- What the agent got wrong: initially used `python`, absent from PATH; shell output caught it. The venv base interpreter then hit sandbox access denial; execution approval allowed the offline generator and tests. An interrupted turn lost the first test session, so the full suite was rerun rather than claiming a missing result.
- Review caught two incorrect evidence paths (`guards.py` and `redflags.py` live under `services/agent/w1/`) and a first-patient-only bootstrap check that missed partial cohorts. Paths were corrected; the bootstrap now inserts missing patients and preserves existing consent expiry, covered by `infra/db/tests/test_migrate.py::test_migrations_copy_rollback_and_restart`.
- No `.env` was read, printed or edited; no eval runner, OpenRouter call or Docker build ran. Compose config passed with `--env-file NUL`, `COMPOSE_DISABLE_ENV_FILE=1`, `--no-env-resolution`, `--no-interpolate`, and `--quiet` to avoid dotenv access. Container startup itself was not executed, as requested.
- Saved runs supply every headline and token number; calibrated faithfulness, end-to-end booking, W3 timing, availability, restore, full residency/retention and exhaustive audit/crash proof remain explicit gaps. Page 16/34 requirements use the supplied task; the source deck was not independently available. Pip caching does not guarantee byte-range resume of every interrupted download.
- Verification: full `python -m pytest` with `DATABASE_URL=postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog` and `PYTHON_DOTENV_DISABLED=1`: **242 passed, 13 warnings in 48.99s**; no failures or skips. Warnings are dependency deprecations. Compose config validation passed (exit 0). Human review fields intentionally unfilled.
- Day-8 docs notes: the first combined patch used an incomplete log heading as context; apply_patch rejected it before writing any files. The files were then created and reviewed against the requested checklist and ADR-001. No source deck was available for independent p.43 verification; content follows the supplied task.
- Day-8 docs verification: the first pytest invocation hit a sandbox base-interpreter access denial; the tool error caught it and the approved rerun of the full `pytest` suite, with the required DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`, reported **242 passed, 13 warnings in 30.55s**. `git diff --check` passed. No eval runner, OpenRouter call, Docker build or `.env` access. Human review fields remain untouched.
- Executive-memo evidence review: latest W1 scores are supported, but 34 is the whole set (12 emergency-desk cases plus one crisis-line red flag); specialty accuracy is scored on 16 booking cases. Latest W2 supports 30 cases, zero scope leaks and correct page citations, not universal safety or calibrated faithfulness. The PRD labels 41%, 9,000/day, 2.4M PDFs/year and 11 minutes as client figures pending discovery, contradicting the memo's blanket eval provenance. Production booking performance, exhaustive read auditing, verified residency/full de-identification, a one-day vendor swap, provider-caused latency variance, all-target W2 success, the quoted retry motivation and uniqueness of the user-facing incident cannot be established from this evidence. SQL scope, consent checks, the documented retry incident and fixes, model-selection history, token counts and rounded latency values have bounded repo support. Timeout/fallback under 4 s remains a plan; tracing and a 15 s timeout already exist.
- Executive-memo notes (what the agent got wrong and what caught it): initial file searches assumed a top-level `tests` directory and `services/api/models.py`; `rg` reported those paths absent and the review used the actual distributed test paths and request models in `main.py`. The first full pytest invocation encountered the sandbox base-interpreter denial; execution approval resolved it. No memo wording or numbers were corrected despite evidence caveats, as explicitly requested.
- Executive-memo verification: full `pytest` with the required local DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **242 passed, 13 warnings in 23.85s** (dependency deprecations; no failures or skips). No `.env` access, eval execution, OpenRouter call or Docker build. Updated this open PR entry; human accepted/rejected/why fields remain untouched.
### PR #12 — Pro: model resilience — 2026-11-02
agent: Copilot SDK assistant (model not recorded); Codex (GPT-6) for README opening
proposed:
- Add per-workflow model deadlines, one scrubbed hedge, a two-call-per-request cap, per-(workflow, model) circuit breakers, and model/provider/serving-role usage records.
- Degrade W1 outages to front-desk escalation only after deterministic red-flag rules; return a scoped, unverified verbatim-lines fallback for strong W2 report matches.
- Expose degraded API responses in `X-Degraded` and structured logs; record `served_by`, fallback/degraded rates, and p95 spread in eval output and the offline report.
- Document the new settings, outage behavior and denial-of-service control; add fake-client and workflow regressions.
- Add Pro observability: PHI-free Postgres tracing, role-gated sourced `/ops` metrics and trace trees, a 30-day purge function, and a saved-run cost worksheet using model-specific environment prices.
- Serve `/ops` and `/frontdesk` as public static shells; move their role-protected data endpoints under `/ops/api` and `/frontdesk/api`, with demo bearer tokens held in session storage.
- Render sourced ops tiles as human-readable values with status colors and latency targets; show traces as nested duration trees, and configure demo auth through `.env.example` and the README.
- Codex (GPT-6): replace only the README opening with the user-supplied overview and design principles, adding the requested current-eval link text and W1 specialty-accuracy regression disclosure; preserve all content from Layout onward.
- Submission freeze updates: replace the tier summary, add known gaps and the Day-8 change-request status, and add the p.47 checklist and synthetic demo run block in `docs/submission.md`.
- Add synthetic-data and scheduling READMEs: document seeded-code generation without LLM prompts, link the generator scripts and PR #2 decision, and point to the booking store, database `book_slot()` and ADR-002.
accepted: provider-distinct hedge, rate-based breaker, 8 s outage-only deadline, in-region tracing, /ops screen, cost sheet.
rejected: the 3.5 s deadline + count-based breaker, Langfuse Cloud, auth-gated HTML page shells.
why: runs 172538/172623 regressed availability (6 of 16 bookings to front desk); runs 180728/180812/180851 restored it; residency; the browser 401.
notes:
- What the agent got wrong: the first focused run exposed a race where an already-successful fallback could finish after the wait result was captured; `test_primary_error_uses_fallback` failed and caught it. The wait loop now rechecks all completed futures before declaring both branches failed.
- Verification: focused fake-client/workflow tests passed (74 passed); final full `pytest` with the required local DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **255 passed, 13 warnings in 23.86s** (no failures or skips). Pylance found no syntax errors or type errors in the changed model and node modules.
- The saved-run report was regenerated offline; no eval runner, OpenRouter request, Docker build or `.env` access. The existing untracked executive memo was left untouched. Human accepted/rejected/why fields remain for the pod.
- README opening notes (Codex): no implementation changes or new factual claims were drafted by the agent; wording follows the user's supplied text. The existing detailed outage section distinguishes W1 front-desk escalation from W2 verbatim fallback. No agent error identified during this edit; preservation of the lower README is checked by a before/after SHA-256 comparison. Unrelated existing eval/report and memo changes remain untouched; human review fields remain untouched.
- README opening verification: matching SHA-256 hashes confirm all text from `## Layout (deck p.48)` onward is unchanged. Full `pytest` with the required local DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **255 passed, 13 warnings in 38.06s**. `git diff --check` passed. No `.env` access, eval execution, model request or Docker build.
- W1 regression correction: restore the 8-second W1 outage deadline, configure provider-distinct hedges, count logical request outcomes in the breaker, and report availability alongside both specialty-accuracy views. The finding: a latency fix regressed availability; caught by running the eval 3 times; diagnosed from saved runs 172538 and 172623. Those saved records lack per-call completion timestamps, so they cannot prove whether the successful unselected hedges arrived before the 3.5-second deadline.
- What the agent got wrong: the previous breaker counted failed primary attempts even when a hedge rescued the request, allowing the breaker to short-circuit while service was answering. The saved 172623 run exposed the resulting 0.005-second `served_by=none` degradation; new fake-response tests cover five rescued timeouts, ten real failures, and a pre-deadline hedge completed before a delayed coordinator wake-up.
- What the agent got wrong during the correction: the first coordinator rewrite could declare failure after seeing one completed future while another completed between polling and failure checks. The focused `test_primary_error_uses_fallback` and `test_five_primary_timeouts_rescued_by_hedge_do_not_open_breaker` regressions caught it; the loop now rechecks all futures before deciding.
- Verification: full `pytest` with the required local DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **261 passed, 13 warnings in 28.74s** (zero failures; warnings are dependency deprecations). The focused hedge/metrics/report tests passed, the report was regenerated from saved JSON only, and no eval runner, OpenRouter request, Docker build or `.env` access occurred.

- Executive-memo refresh (Codex, GPT-6): cite saved W1 runs 20261102-180728, 180812 and 180851 for 16/16 specialty routing, replace stale latency figures with the requested 2.6-4.1 s sentence, and add the requested availability-regression lesson. No "before our latency change" qualifier was present in the working memo.
- Claim re-check: the three saved runs report specialty_accuracy=1.0, availability=1.0 and zero degradation; p95 values 2.88, 2.56 and 4.09 s round to the requested range and meet <4 s in two of three. The earlier 172454/172538/172623 series supports the regression narrative. W1's synthetic shortlist measures routing, not live calendar availability.
- What the agent got wrong / evidence caveat: the requested "24% of bookings" wording conflates denominators. Saved run 172623 records eight front-desk degradations among 34 triage cases (23.5%, rounded 24%), including six of 16 expected bookings (37.5%) and two clarification cases. Preserved the user's exact requested sentence; the saved failure table and evals/run_w1.py metric denominator expose this limitation. Human accepted/rejected/why fields remain untouched.
- Executive-memo refresh verification: full `pytest` with the required local DATABASE_URL and `PYTHON_DOTENV_DISABLED=1`: **261 passed, 13 warnings in 74.97s (0:01:14)**. The initial sandbox invocation could not access the base Python interpreter; the execution error caught it and the approved rerun succeeded. `git diff --check` passed. No `.env` access, eval execution, model call or Docker build.
- Pro observability implementation: migration 007 adds the in-region `trace_span` schema, 30-day purge function, and audit trace-context trigger; graph nodes, tool/model calls and HTTP requests use a `SpanExporter`. Ops endpoints are role-gated and omit unsourced tiles. The worksheet uses saved W1/W2 evaluation JSON only and the model-specific `PRICE_*_PER_MTOK_*` settings. ADR-005 records Postgres now, self-hosted Langfuse as a future exporter, and rejection of Langfuse Cloud for residency.
- What the agent got wrong: the first cost-sheet test expected the local-embedding description in an adjacent phrase; `evals/tests/test_build_cost_sheet.py::test_cost_sheet_uses_measured_runs_and_workflow_prices` caught the brittle assertion. The assertion was corrected to verify the generated model and no-API-cost content.
- Migration application: the local database had the 001–006 schema/data but no ledger; catalog checks confirmed existing migration effects before reconciling 002–006, then `tools.migrate.migrate` applied 007 and verified `trace_span`, its workflow index and ledger entry.
- Observability verification: focused API, PostgreSQL, cost-sheet and eval-report tests passed (16 passed). Required full `pytest` with `DATABASE_URL=postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog` and `PYTHON_DOTENV_DISABLED=1`: **266 passed, 13 warnings in 38.43s** (zero failures; warnings are dependency deprecations). No `.env` access, eval execution, OpenRouter request or Docker build.
- What the agent got wrong: the first Windows focused run used the oversized generated ID for the 10 MB upload parameter, exceeding the process environment-variable limit. Concise parametrized IDs in `test_upload_rejection` resolved the test-runner issue without changing test inputs or assertions. The first full run also used the global pytest instead of the repository `.venv`; it lacked `psycopg_pool` and hit a subprocess handle error. The full suite passed when run with `.venv\Scripts\pytest.exe`.
- Ops/frontdesk verification: the focused API, frontdesk, and Postgres tests passed (49 passed). Full `pytest` with the required `DATABASE_URL` and `PYTHON_DOTENV_DISABLED=1`, using the project virtualenv: **274 passed, 13 warnings in 55.41s** (zero failures). The new tests verify public HTML shells, absence of seeded patient data, and 401/403 behavior across every `/ops/api` and `/frontdesk/api` route. No `.env` access, eval execution, OpenRouter request, or Docker build.
- Ops page verification: inline JavaScript passed `node --check`; the in-browser rendering check was done by the pod, not the agent.
- Static-page encoding correction: serve all HTML shells with `text/html; charset=utf-8`, repair the root page's mojibake separator and legacy bytes, and place its UTF-8 declaration first in `<head>`. Tests verify response charset, strict UTF-8 byte decoding, and charset-meta placement for each static page.
- What the agent got wrong: the root page had a CP1252 ellipsis and a standalone `0xB7` separator byte, while its charset declaration was outside a `<head>`. The static-page API tests now check the served bytes and header.
- Static-page verification: full `pytest` with the required local `DATABASE_URL` and `PYTHON_DOTENV_DISABLED=1`, using `.venv\Scripts\pytest.exe`: **274 passed, 13 warnings in 55.90s**. `git diff --check` and strict UTF-8/meta checks passed. No `.env` access, eval execution, model request, or Docker build.
- Submission-freeze update: replace the Core-only tier summary, add Known gaps and the Day-8 change-request status, and add the p.47 checklist and local synthetic demo instructions in `docs/submission.md`.
- What the agent got wrong: the README's old tier sentence omitted the Pro platform items; the submission request caught the mismatch. The API tests already checked response charset and UTF-8 decoding, but not the actual My records separator, so `test_static_page` now asserts the `·` join and rejects `�`.
- Submission-freeze verification: full `.venv\Scripts\pytest.exe` with the required local `DATABASE_URL` and `PYTHON_DOTENV_DISABLED=1`: **274 passed, 13 warnings in 49.85s** (zero failures). `git diff --check` and static UTF-8/meta checks passed. No `.env` access, eval execution, model request, or Docker build.
- What the agent got wrong: no documentation errors were identified; generator links were checked against the source files, and the booking description was checked against `store.py`, `001_schema.sql` and ADR-002.
- Documentation verification: README paths and cross-links were checked against repository files; `git diff --check` passed. Documentation-only change; tests were not run. Human accepted/rejected/why fields remain untouched.

### PR #13 — Pro: W3 scribe with doctor interrupt and edit history — 2026-11-02
agent: Copilot SDK assistant (model not recorded); Codex (GPT-6) for D7 egress de-identification
proposed:
- Generate 300 seeded dictations and 40 fixed synthetic fixtures, plus per-clinic nightly stock CSVs.
- Add CSV/API stock adapters and a W3 graph that scrubs, runs deterministic red flags before the model, grounds medicine details, and rechecks edits before review.
- Persist editable drafts and edit rows separately from immutable signed notes; bind signing to the treating doctor, hash notes in Postgres, link later versions, and audit signing/notification intent.
- Configure the W3 model default and 20-second deadline without changing W1/W2 workflow defaults; add a schedulable timeout command and document the graph/schema.
- Wire the existing W3 graph/store into a doctor-scoped API and static console; add a 40-case W3 eval/report pipeline, sourced Ops review/edit metrics, and W3 cost reporting.
- Export the compiled W3 graph and update the architecture, README tier text, D7, and stock-file ignore/mount configuration.
- W3 fix per diagnosis of eval run 20261102-220544 (pod rule: a molecule is supported only if the transcript states it, or a transcript-stated brand maps to exactly one formulary composition; the model never supplies a molecule): formulary lookup with `source` provenance (`transcript` | `formulary:<product>`), `molecule_unresolved`/`molecule_ambiguous` flags, class mentions kept even with an invalid quote, prompt `w3-prompts-v2` (S/O/A/P roles, P never empty, quote holds name + final dose), molecule+strength stock matching with `unknown` (grey in the console), harness fixes (sentinel normalised, `invented_molecules` = no transcript and no formulary support, initial model JSON, provenance and expected-vs-actual stock saved), eval set v2 (markers removed, SOAP-field gold, `set_version` 2), T17 task card.
- D7 / ADR-001 trigger c: add pinned Presidio/spaCy egress de-identification, custom Indian identifier recognisers with Verhoeff Aadhaar validation, typed in-process tokens and recursive local JSON reply restoration on W1/W2/W3 primary/hedge calls. Cover system messages, retain the original regex refusal gate, unify the legacy adapter import, and stop discarding W3 identity maps upstream.
- Add numeric-only deid_ms tracing (migration 011, applied locally, and ops trace tree), deterministic offline replay with independent regex/Presidio detection and a zero-hit command gate, clinical-number/privacy/restoration/timing tests, and D7/ADR/deployment/container evidence.
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- What the agent got wrong: the first W3 edit-resume test showed LangGraph had discarded the doctor edit list because `edits` was absent from `W3State`; `test_later_edit_creates_version_two_with_supersedes` caught it. The field was added to the typed state and the test passed.
- What the agent got wrong: the first bulk generator scanned all 246,068 formulary rows for every transcript and exceeded three minutes; the running command exposed the cost. A first-token index reduced the repeated work and the generator then produced all 300 records and 40 fixtures. The initial stock generator run also caught a missing repository import path (`ModuleNotFoundError: data`), fixed before successful CSV generation.
- What the agent got wrong: the first PostgreSQL API integration run returned HTTP 500 because `deps.py` used `json.dumps` for durable idempotency responses without importing `json`; `test_postgres_pick_slot_resume_and_replay` caught it. The import was added and the focused PostgreSQL/API/schema/eval tests passed. The first ignore check also found `data/synthetic/out/` absent from `.dockerignore`; it is now excluded there and remains covered by `.gitignore`.
- W3 API exposure verification: focused PostgreSQL/API/schema/eval tests passed (20 passed); full `.venv\Scripts\pytest.exe` with the required local `DATABASE_URL` and `PYTHON_DOTENV_DISABLED=1`: **296 passed, 13 warnings in 43.19s**. `git diff --check` passed. `evals/run_w3.py` was not run as requested.
- Verification: focused W3, fixture, and PostgreSQL migration tests passed (26 passed). Final `.venv\Scripts\pytest.exe` with `DATABASE_URL=postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog` and `PYTHON_DOTENV_DISABLED=1`: **289 passed, 13 warnings in 60.65s**. `git diff --check` and timeout worker import passed.
- Applied `008_w3_drafts.sql` and `009_w3_draft_version_retry.sql` to the local database with `tools/migrate.py`. Generated output contains 300 dictations, 40 fixed fixtures, 25 represented specialties, and 28 per-clinic stock CSVs; generated output remains git-ignored.
- No `.env` access, OpenRouter request, eval run, or Docker build. Pylance diagnostics timed out; focused and full pytest runs validated Python behavior. Human review fields remain unfilled.
- What the agent got wrong: several executable entry points did not initialize `.env`; the reported W3 `OPENROUTER_API_KEY is not set` failure and a scan of environment-backed CLI configuration caught the gaps in `evals/run_w3.py`, `tools/migrate.py`, `tools/build_cost_sheet.py`, and the W3 timeout worker. Added `load_dotenv(ROOT / ".env")` inside each `main()` (dotenv's default does not override already-set variables).
- Full `.venv\Scripts\pytest.exe` with `DATABASE_URL` set and `PYTHON_DOTENV_DISABLED=1`: **296 passed, 13 warnings in 29.97s**; the suite did not load `.env`. `evals/run_w3.py` was not run.
- What the agent got wrong (W3 fix): the first grounding draft accepted any molecule found in the quote, so the model's `molecule: "Atorva"` (the brand echoed, the exact failure in run 20261102-220544) would have passed as "stated in the transcript"; the new brand-echo regression test caught it. `is_brand_only()` now rejects a stated molecule that only names formulary products, and with no formulary deployed (`data/clean` is dockerignored) a name echoed as the molecule fails safe to the placeholder.
- What the agent got wrong (W3 fix): the harness compared molecules to the raw `[doctor to specify]` string after `_key()` had already turned it into `doctor to specify`, so every placeholder counted as a real molecule; the diagnosis of the saved run exposed it, and `test_placeholder_sentinel_is_normalised_before_comparison` covers it. Placeholder stock rows were also written as `stock_status`, which the console never reads (`status`), so unknown always rendered by accident; both paths now write `status`.
- Behaviour changes the pod should check: `test_stock_only_lists_in_stock_same_molecule_alternatives` previously expected an unstocked brand to be `out_of_stock`; it now expects `unknown` per the new rule. `stock_flag_correct` now also counts gold medicines with an unresolved molecule (expected `unknown`), and `test_w3_field_f1_and_metrics_use_gold_fields_and_stock_source` was updated to 2/2. Stock alternatives now require the same composition including strength (the old key dropped strengths and offered 850/1000 mg for 500 mg). A doctor-typed molecule is still re-grounded on edit and can be reset to the placeholder; not changed here. Eval set v2 gold (SOAP fields, brand-only molecules) is drafted by the agent: clinical review pending.
- Verification: full `.venv\Scripts\pytest.exe` with the required local `DATABASE_URL` and `PYTHON_DOTENV_DISABLED=1`: **329 tests, 0 failures, 0 errors, 0 skipped in 85 s** (from `--junitxml`; the plain summary line did not print). The first full run hung at the first PostgreSQL test because the `infra-db-1` container had exited; it was restarted with `docker start` (no image build). No `.env` access, eval run or OpenRouter request; the old run file `w3-20261102-220544.*` is kept for history.

- D7 notes (Codex): focused `test_primary_error_uses_fallback`, `test_default_fallback_retries_same_model_with_provider_fallbacks` and `test_all_failed_hedges_raise_unavailable` exposed the coordinator polling completed failed futures while the hedge did NLP. Waiting only on pending futures fixed the starvation without weakening deadlines or tests.
- D7 notes: `test_postgres_pick_slot_resume_and_replay` caught missing migration 011 in its isolated schema (UndefinedColumn deid_ms and no stored spans). The fixture now applies 011; all existing assertions remain. W3 source inspection caught its earlier regex scrub discarding the only restoration map; source stays local and the adapter owns restoration.
- D7 notes: the unchanged <50 ms p95 assertion caught 64.1 ms under concurrent replay load and 51.3 ms in a later full run. Profiling found duplicate tok2vec inference; the local pipeline now runs NER with its embedded representations, omitting unused tagger/parser/lemma stages and standalone tok2vec. Custom Indian phone patterns avoid unnecessary worldwide phone validation. Regex flag inspection also caught Presidio's case-insensitive default for the explicit-name pattern; a clinical symptom regression now protects it.
- D7 offline assurance: dependency inspection found the default email recogniser can refresh a public suffix list. Production uses a local custom email pattern; the independent scanner uses its own email regex plus Presidio person/phone recognition. The replay test forbids socket connections. Missing local spaCy models fail startup rather than downloading.
- D7 replay: `egress-20261103-010825` contains aggregate metrics only: 200 seeded synthetic W1/W2/W3 payloads, zero independent-detector hits/refusals, recall 1.0 for all ten generated identifier types, and 0.0 false-positive rate on the four specified clean clinical examples. Median 7.2 ms, p95 8.5 ms, max including first-use 17 ms. Statistical names and arbitrary contextual quasi-identifiers remain residual risks; no deployment-residency claim.
- D7 verification: full `.venv\Scripts\pytest.exe -o addopts=''` with the required localhost:55432 DATABASE_URL and PYTHON_DOTENV_DISABLED=1: **337 passed, 13 warnings in 69.16s (0:01:09)**. No test was weakened or deleted. Only the requested offline replay ran; no model evals, OpenRouter calls, browser use, Docker image build or `.env` access. Pre-existing cost-sheet/report and W3 run changes were preserved; human accepted/rejected/why fields remain untouched.

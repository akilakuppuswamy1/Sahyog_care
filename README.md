# Sahyog Care Companion

An AI care companion for Sahyog's two-city hospital network. Patients describe symptoms in English or Hinglish and
book the right doctor without a phone call, and ask questions of their own medical reports with every answer cited
to the page it came from.

**Design principles**
- **Rules before models** — red-flag rules decide emergencies before any model is called; the model may add a flag,
  never remove one.
- **Isolation in the database** — every retrieval is filtered by the signed-in patient in SQL; caregivers see records
  only while consent is valid; every record read is audited and visible to the patient.
- **Degrade, never fail silently** — model calls have deadlines, a hedged fallback and a circuit breaker; outages
  route to the front desk.
- **Measured, not asserted** — golden evaluation sets written before the code; results in `evals/report.md`, generated
  from saved runs.

**Tier reached: Pro** (`pro-complete`). Built: W3 text-transcript scribe with doctor review/sign-off,
durable queued ingest and a separate worker, shared Presidio de-identification, model resilience,
Postgres tracing and Ops, calibrated W2 judging, and the sourced cost worksheet.
Pro is the delivery milestone; it does not mean every evaluation target is met or production deployment is ready.

## Known gaps

- No speech-to-text or audio lifecycle: transcripts are text input.
- W3 field F1 0.82, medicine precision/recall 0.83/0.83 remain below the 0.9 target
  ([run 20261103-114035](evals/runs/w3-20261103-114035.json), [targets](evals/report.md)).
- W2 faithfulness 0.868 remains below 0.9, despite qualifying held-out judge calibration
  ([run 20261103-104837](evals/runs/w2-20261103-104837.json),
  [calibration 20261103-061312-389012](evals/runs/judge-calibration-20261103-061312-389012.json)).
  The [judge-zero human review](evals/runs/judge-review-w2-20261103-094651.md) records both an answer defect
  and judge errors in an earlier run; calibration does not make the judge infallible.
- Remaining Architect items include verified India hosting, encryption at rest, least-privilege database roles,
  distributed limits, retention validation and recovery drills. The MCP scheduling/stock surface is built, but still
  uses demo identity and in-process limits. Production identity/booking OTP and SMS delivery also remain unbuilt;
  W3 timeout and trace-purge commands are not scheduled.
- Booking completion/time and doctor review time remain unmeasured. See [evaluation report](evals/report.md)
  and [D7 residual risks](docs/d7-threat-model.md).
- **Day-8 change request:** Not received at submission; [template](docs/change-request/).

## Layout (deck p.48)
```
docs/       prd-lite.md · rfp-response.md · adr/ · diagrams/
            task-cards.md (D1) · discovery-questions.md (D2)
            assumptions.md (D3) · data-contracts/ (D4)
data/       inventory.md · inventory.py · transforms/ · synthetic/prompts/ · raw/ (git-ignored)
services/   api/ · agent/ · mcp/ · scheduling/ · records/ · scribe/
evals/      sets/ · runs/ · report.md
infra/      docker-compose.yml · deployment-adr.md
memo/       executive-memo.md
AI-COLLABORATION.md
```

## Start

1. Copy `.env.example` to `.env` and add your key.
2. Run `docker compose -f infra/docker-compose.yml --profile app up -d --build`.
3. Open http://localhost:8000.

The shared Python 3.14 image runs migrations and W2 fixture ingest before the API starts. First startup downloads local bge-m3. BuildKit caches pip downloads between builds; interrupted downloads may still need to restart.

## Local development (Windows PowerShell or bash, from the repo root)
```bash
python -m venv .venv && .venv\Scripts\activate      # bash: source .venv/bin/activate
pip install -r requirements.txt
docker compose -f infra/docker-compose.yml up -d db   # Postgres 16 + pgvector; schema applied on first start
python data/synthetic/generate_doctors.py            # 500 doctors
python data/synthetic/generate_slots.py              # 480,000 slots
docker compose -f infra/docker-compose.yml exec -w /repo db psql -U sahyog -d sahyog -v ON_ERROR_STOP=1 -f infra/db/002_load_generated.sql
```
The repo is mounted read-only at `/repo` inside the db container, so you don't need a local `psql`.

The database listens on host port **55432** (not 5432, which a native Postgres often holds). If 55432 is busy too,
pick another: `$env:DB_PORT = "56000"` before `docker compose ... up -d db`, and use the same port in DATABASE_URL.
Check a port with `Get-NetTCPConnection -LocalPort 55432 -ErrorAction SilentlyContinue` (no output means free).

Run the tests (the database tests run only when DATABASE_URL is set):
```bash
$env:DATABASE_URL = "postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"   # bash: export DATABASE_URL=...
pytest
```

### Run the app

- Front desk: apply `infra/db/005_frontdesk.sql`, open `/?view=frontdesk` (the staff shell; `/frontdesk` also serves it with a staff bearer header), and use a staff ID (`Bearer demo-staff-FD01`, demo auth only) to review hand-offs and override bookings.
- Upload: in **My records**, choose **Upload a report** (text PDF, up to 10 MB). `POST /records/upload` only enqueues and returns HTTP 202 `{job_id, doc_id, status:"queued"}`; the page polls the owner-only `/records/api/jobs/{job_id}` every two seconds. Other patients receive 404. Uploads are stored in git-ignored `data/uploads/` (a shared `uploads` volume in Compose).
- Scribe: generate the synthetic clinic stock CSVs with `python data/synthetic/generate_stock.py` (large outputs are ignored by Git and Docker), apply migrations with `python tools/migrate.py`, then open `/scribe`. Compose mounts that host directory read-only; set `W3_STOCK_DIR` when using a different location. Sign in with a doctor ID and use `Bearer demo-doc-<DOCTOR_ID>` demo authentication. The API requires the treating doctor's encounter ID; drafts remain unsigned until approved.


After the database setup and W2 fixture ingestion below, run locally from the activated venv:

```powershell
$env:DATABASE_URL="postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"
python -m uvicorn services.api.main:app --port 8000
```

Apply migration `012_ingest_jobs.sql` with `python tools/migrate.py`. In a second
activated-venv terminal, set the same `DATABASE_URL` and start the local worker:

```powershell
$env:DATABASE_URL="postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"
python -m services.records.worker
```

The worker runs local bge-m3 ingestion, retries up to three attempts with 1 s / 2 s
backoff, and records a safe `last_error` on failure. A disconnected worker's job
is recoverable; Ctrl+C lets active ingestion finish before stopping. Identical PDF
content for the same owner reuses its document and job across API restarts. The
in-process ingest callback is reserved for explicitly injected test dependencies.
The app Compose profile starts `worker` after `migrate` using the API image and
shared upload volume. Ops shows queue depth and oldest queued age from `ingest_job`
when that table has rows.

The copied repo-root `.env` supplies `SAHYOG_DEMO_AUTH=1` for local runs; do not set it in production. Supply `OPENROUTER_API_KEY` and the model environment variables there as well. The production dependency builder loads `.env` without overriding existing environment variables; test dependencies do not load it. Open http://localhost:8000 and sign in with a synthetic patient ID such as `PT-W2-0001` (the dev-only bearer stub is enabled by `.env`).

### MCP scheduling and stock

The API mounts the Streamable HTTP MCP server at `http://localhost:8000/mcp/`. Configure
`MCP_APPROVED_AGENTS` in the local environment with the exact `X-Agent-Id` values your
demo clients will send; the `.env.example` values are demo-only. The server requires
the same demo bearer tokens as the API on every MCP HTTP request. This is not production
authentication. Run `python tools/migrate.py` to apply migration 013, which allows the
ops role in audit events.

The MCP tools share the API's scheduling store, `book_slot()` idempotency, W3 stock
adapter, patient/IP rate limiter, audit table and trace exporter. See
[docs/mcp.md](docs/mcp.md) for tool schemas, role boundaries and client setup, and
[ADR-006](docs/adr/ADR-006-mcp-tool-server.md) for the architecture decision. To run
the server separately, use `python -m services.mcp.server` (Streamable HTTP on port
8001). To exercise the mounted API with a small MCP client:

```powershell
python tools/mcp_demo.py --base-url http://localhost:8000 --agent-id agent-demo
```

The demo client uses synthetic patient/front-desk bearer tokens and reads no `.env`.
Production deployments must configure their host/origin allowlists and replace demo
authentication before exposing the service to agents.

### When the model is slow or down

W1 has an 8-second outage deadline and starts one scrubbed hedge after 2 seconds; W2 keeps its 6-second deadline and 3-second hedge default. Set `OPENROUTER_PROVIDERS_W1` and `OPENROUTER_PROVIDERS_W2` to comma-separated provider names from the chosen model's OpenRouter page. The first provider is pinned for the primary request, and the hedge excludes it so it cannot use the same provider. If a provider list is unset, the client retains its existing routing behavior and logs a warning once.

The circuit breaker measures logical request outcomes, not individual hedge attempts: it opens for 30 seconds only after at least 10 calls in 60 seconds with a failure rate of 50% or more, then allows one half-open probe. A request rescued by its hedge counts as a success, even if its primary timed out. W1 sends a case to the front desk without booking only when no valid model result arrives before the outage deadline, after deterministic red-flag rules run. If W2 is unavailable for a strongly matched report question, it returns only verbatim lines from that patient's matched report chunks with Sources and marks the answer unverified; weak matches remain not found. Degraded API responses carry `X-Degraded: true`, and the request log records `degraded`. The eval report keeps specialty accuracy over all routine cases, adds specialty accuracy when answered and availability (`1 - degraded_rate`), and tracks a degraded-rate target of at most 0.02.

### Pro observability and cost worksheet

Migration 007 stores allowlisted request, graph-node, tool and model spans in Postgres. Spans contain
no prompts, questions, symptom text or patient IDs; the audit log maps trace IDs to patients. To
apply pending SQL migrations and seed the local fixtures, run `python tools/migrate.py`.

The ops JSON endpoints (`/ops/api/summary`, `/ops/api/traces/{trace_id}`) require the dev-only
`Bearer demo-ops-<ID>` role; patients and front desk receive 403. Open `/?view=ops`, enter the ops
ID, and load the sourced tiles. The direct `/ops` page serves the same static shell without authentication; its data requests remain role-protected.
Every displayed tile includes its source; unavailable metrics are omitted. `OPS_REVIEW_SLA_MINUTES`
sets the escalation review SLA (defaults to the existing front-desk SLA setting).

Run `python tools/build_cost_sheet.py` to regenerate [docs/cost-sheet.md](docs/cost-sheet.md) from
saved evaluation JSON; it does not call a model or run evals. Set `PRICE_IN_PER_MTOK_W1`,
`PRICE_OUT_PER_MTOK_W1`, `PRICE_IN_PER_MTOK_W2`, `PRICE_OUT_PER_MTOK_W2`,
`PRICE_IN_PER_MTOK_W3`, `PRICE_OUT_PER_MTOK_W3`, and `PRICE_DATE` to
populate estimates. Unset prices remain formulas marked “to fill.” The per-consultation example is
an explicit one-triage-turn plus one-record-answer scenario, not an observed workflow mix; W3 is
reported separately per scribe draft.

Trace retention is 30 days. Schedule `SELECT purge_trace_spans();` on the local database (for
example, daily); the migration creates the purge function but does not install a scheduler. Traces
must remain in-region; self-hosted Langfuse may replace the exporter, while Langfuse Cloud is
rejected for residency. See [ADR-005](docs/adr/ADR-005-tracing.md).

Or use Docker:

```powershell
docker compose -f infra/docker-compose.yml --profile app up -d --build
```

Compose supplies the demo auth flag and database URL, and optionally loads the existing `.env` for model settings. The `hfcache` volume caches the local bge-m3 download (about 2.3 GB). First startup loads the model before serving requests. Use one API worker for Core; demo authentication is not suitable for a public deployment.

## Getting the data
1. Create a Kaggle API token and place it at `~/.kaggle/kaggle.json`.
2. Run:
   ```bash
   pip install kaggle
   for s in joniarroba/noshowappointments tboyle10/medicaltranscriptions \
            itachi9604/disease-symptom-description-dataset \
            shudhanshusingh/az-medicine-dataset-of-india prasad22/healthcare-dataset; do
     kaggle datasets download -d "$s" -p "data/raw/${s#*/}" --unzip
   done
   python data/inventory.py
   python -m data.transforms.run_all      # writes data/clean/ and tallies.json
   ```
3. Paste the counts and licences into `data/inventory.md`.

## Discovery checklist (deck p.10)
- [x] D1: 12 client tasks plus at least 6 team tasks, each on a rung (`docs/task-cards.md`)
- [ ] D2: 10 questions posted and answered (`docs/discovery-questions.md`)
- [x] D3: at least 8 rows, each with an owner, a validation and a fallback (`docs/assumptions.md`)
- [ ] Data inventory with exact row counts and licences
- [x] Repository in the p.48 structure
## W1 Find my doctor (step 4)
```powershell
copy .env.example .env        # then fill OPENROUTER_API_KEY, OPENROUTER_MODEL, DATABASE_URL (your port)
pip install -r requirements.txt
pytest                                    # node tests + graph tests (scripted model, no network, no cost)
python -m evals.run_w1 --rules-only       # red-flag recall, free
python -m evals.run_w1                    # full eval through OpenRouter -> evals/runs/w1-*.md
python -m services.agent.w1.cli           # talk to it; pauses for the slot pick; --resume <thread> after a restart
```
## W2 My records (step 5)
```powershell
pip install -r requirements.txt
docker compose -f infra/docker-compose.yml exec -w /repo db psql -U sahyog -d sahyog -v ON_ERROR_STOP=1 -f infra/db/004_w2_records.sql
python data/synthetic/generate_reports.py --bulk-patients 50     # 10 fixture + ~200 bulk report PDFs
python -m services.records.ingest --load-patients                # patients + caregiver consents
python -m services.records.ingest                                # parse, chunk, embed (local bge-m3), index
pytest                                                           # includes the red-team retrieval test on pgvector
python -m evals.run_w2                                           # 80 W2 cases through OpenRouter -> evals/runs/w2-*.md
python -m services.agent.w2.cli --as PT-W2-0001                  # ask Priya's records
python -m services.agent.w2.cli --as PT-W2-0001 --for PT-W2-0002 # Priya as caregiver for her mother
```
## W2 faithfulness judge and pod calibration (Pro)

W1 and W2 each contain 80 synthetic draft cases. Existing cases are preserved; additions carry
`agent draft — clinical review pending`. The sets include Hinglish, caregivers, negation traps,
records requests in triage, numeric ranges, cross-patient requests and prompt injections.

The original 30-item `evals/sets/judge_calibration.jsonl` is the prompt-development set.
Judge prompt `w2-faithfulness-v2` is frozen for the held-out round. Calibration uses a
held-out set because tuning and testing on the same items would overfit.
The 20 `judge-cal-r2-001..020` items in `evals/sets/judge_calibration_holdout.jsonl`
were labelled by the pod (human) on 2026-11-03 before seeing judge output. Obsolete
unlabelled drafts are gone; the category-only post-hoc analysis key now matches these
item IDs and must never be supplied to the judge. Freeze the prompt first:
tuning and calibrating on the same items would overfit. If held-out results inform further tuning,
use a fresh held-out set next. Faithfulness is support for every claim, not completeness/helpfulness.
Measurements answer "What is X?"; true excerpt-scoped absence and printed-range comparisons count.
Ordinary rounding is allowed unless exactness or a changed boundary comparison makes it misleading.
Score whether **every claim is supported by the supplied excerpts**, including units, values and
diagnosis wording. Both sets include deliberately unsupported answers. Empty or invalid labels are refused;
the calibration command refuses them before any model call. A constant single-class rating has
undefined kappa and cannot certify a judge.

When explicitly authorized to make model calls, configure the process environment with the API key,
`OPENROUTER_MODEL_W2` (or `OPENROUTER_MODEL`) and `OPENROUTER_MODEL_JUDGE`. The judge must differ
from both the primary and fallback answering models. `OPENROUTER_FALLBACK_MODEL_JUDGE`, if set,
must equal the judge model so calibration remains applicable. These commands use the same scrubbed,
traced JSON egress adapter as the answering graph:

```powershell
python -m tools.calibrate_judge --set holdout  # calls judge AFTER independent labelling/authorization
python -m tools.calibrate_judge --set original # optional development diagnostic; cannot certify
python -m evals.run_w2 --judge              # calls graph and judge; adds binary scores and one-line reasons
python tools/build_eval_report.py          # saved JSON only; no model calls
```

W2 judging covers `answer` and `answer_degraded` routes using only their cited chunks; refusals are
excluded. Saved JSON records raw `judge_score`, case count and judge identity separately from answer
model usage. Reports show faithfulness only when the **latest held-out calibration for the current
frozen prompt version** has kappa >= 0.6 and matches the W2 run's judge model and prompt version;
they include its calibration run id and prompt version. Matching obsolete prompt versions cannot certify.
The saved-run report also lists the original 30-item kappa and prompt version alongside held-out
results, explicitly marking either original-set prompt version as unmeasured when absent.
`--set` defaults to `original`. The command loads repo-root dotenv in `main()` without overriding
existing environment variables. Legacy runs without set metadata are diagnostic only and cannot certify. `--set` also accepts
an explicit JSONL path; only the canonical holdout path receives held-out provenance. Empty/invalid
labels fail before judge construction. Saved held-out calibration [20261103-061312-389012](evals/runs/judge-calibration-20261103-061312-389012.json)
qualifies the current judge; [the report](evals/report.md) shows its kappa and the below-target W2 result.
Without qualifying calibration, reports show
`judge not calibrated (kappa X)` or `not measured`. Content hits and deterministic verification remain
separate proxies. These saved results do not imply any new evaluation was run for this documentation update.

## W3 Clinical scribe (Pro)
```powershell
python data/synthetic/generate_stock.py
python tools/migrate.py
python -m evals.run_w3       # 40 W3 cases; real graph and OpenRouter, writes evals/runs/w3-*.json/.md
```
The W3 eval makes model calls and is not part of pytest. Set `W3_STOCK_DIR` if clinic CSVs are stored
outside `data/synthetic/out/w3/stock`; the API loads only the selected clinic's inventory file.

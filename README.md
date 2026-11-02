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

**Tier reached:** Core, plus Pro platform items: model deadlines with provider-distinct hedge and circuit breaker; in-region tracing; `/ops` screen; cost worksheet; W3 scribe graph/API/doctor console, grounding, stock adapters, signed-note persistence, 40-case eval set, and a schedulable timeout command. W3 timeout-job scheduling is not wired. Other gaps: Presidio egress replay, async ingest queue, 80-case evals with a calibrated judge, Architect tier.

## Known gaps

- Evaluation targets and unmeasured outcomes: [evals/report.md](evals/report.md).
- Security controls and residual deployment risks: [D7 threat model](docs/d7-threat-model.md).
- **Day-8 change request:** Not received at submission; template ready in [docs/change-request/](docs/change-request/).

## Layout (deck p.48)
```
docs/       prd-lite.md · rfp-response.md · adr/ · diagrams/
            task-cards.md (D1) · discovery-questions.md (D2)
            assumptions.md (D3) · data-contracts/ (D4)
data/       inventory.md · inventory.py · transforms/ · synthetic/prompts/ · raw/ (git-ignored)
services/   api/ · agent/ · scheduling/ · records/ · scribe/
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
- Upload: in **My records**, choose **Upload a report** (text PDF, up to 10 MB); the status list polls every two seconds until indexed or failed. Uploads are stored in git-ignored `data/uploads/`.
- Scribe: generate the synthetic clinic stock CSVs with `python data/synthetic/generate_stock.py` (large outputs are ignored by Git and Docker), apply migrations with `python tools/migrate.py`, then open `/scribe`. Compose mounts that host directory read-only; set `W3_STOCK_DIR` when using a different location. Sign in with a doctor ID and use `Bearer demo-doc-<DOCTOR_ID>` demo authentication. The API requires the treating doctor's encounter ID; drafts remain unsigned until approved.


After the database setup and W2 fixture ingestion below, run locally from the activated venv:

```powershell
$env:DATABASE_URL="postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"
python -m uvicorn services.api.main:app --port 8000
```

The copied repo-root `.env` supplies `SAHYOG_DEMO_AUTH=1` for local runs; do not set it in production. Supply `OPENROUTER_API_KEY` and the model environment variables there as well. The production dependency builder loads `.env` without overriding existing environment variables; test dependencies do not load it. Open http://localhost:8000 and sign in with a synthetic patient ID such as `PT-W2-0001` (the dev-only bearer stub is enabled by `.env`).

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
python -m evals.run_w2                                           # 30 W2 cases through OpenRouter -> evals/runs/w2-*.md
python -m services.agent.w2.cli --as PT-W2-0001                  # ask Priya's records
python -m services.agent.w2.cli --as PT-W2-0001 --for PT-W2-0002 # Priya as caregiver for her mother
```
## W3 Clinical scribe (Pro)
```powershell
python data/synthetic/generate_stock.py
python tools/migrate.py
python -m evals.run_w3       # 40 W3 cases; real graph and OpenRouter, writes evals/runs/w3-*.json/.md
```
The W3 eval makes model calls and is not part of pytest. Set `W3_STOCK_DIR` if clinic CSVs are stored
outside `data/synthetic/out/w3/stock`; the API loads only the selected clinic's inventory file.

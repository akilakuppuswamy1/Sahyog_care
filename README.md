# Sahyog Care Companion

A privacy-compliant agentic platform for Sahyog Health Network. The client is fictional.

The platform has three workflows:
- **W1 Find my doctor:** triage, red-flag gate and booking
- **W2 My records:** ingest and cited Q&A
- **W3 Scribe:** SOAP draft and doctor sign-off

Stack: LangGraph 1.2 · Postgres + pgvector · FastAPI · MCP.

**Tier reached:** _not yet. Discovery is drafted and the data estate is built (step 2)._

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

## Quick start (Windows PowerShell or bash, from the repo root)
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

After the database setup and W2 fixture ingestion below, run locally from the activated venv:

```powershell
$env:DATABASE_URL="postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"
$env:SAHYOG_DEMO_AUTH="1"
python -m uvicorn services.api.main:app --port 8000
```

Supply `OPENROUTER_API_KEY` and the model environment variables in the repo-root `.env` or your shell before starting locally. The production dependency builder loads `.env` without overriding existing environment variables; test dependencies do not load it. Open http://localhost:8000 and sign in with a synthetic patient ID such as `PT-W2-0001` (the dev-only bearer stub is enabled above).

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
- [ ] D1: 12 client tasks plus at least 6 team tasks, each on a rung (`docs/task-cards.md`)
- [ ] D2: 10 questions posted and answered (`docs/discovery-questions.md`)
- [ ] D3: at least 8 rows, each with an owner, a validation and a fallback (`docs/assumptions.md`)
- [ ] Data inventory with exact row counts and licences
- [ ] Repository in the p.48 structure
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

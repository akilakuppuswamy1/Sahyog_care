# Submission checklist (deck p.47)

| Item | Path | Status |
|---|---|---|
| Repository | [Repository root](../) | p.48 layout present; local demo setup is documented below. |
| Architecture pack | [docs/architecture.md](architecture.md) | Version 2; built and planned boundaries are distinguished. |
| D1 — Automation ladder | [docs/task-cards.md](task-cards.md) | Draft v0.1; task cards are present. |
| D2 — Discovery questions | [docs/discovery-questions.md](discovery-questions.md) | Ten questions drafted; responses remain pending. |
| D3 — Assumptions | [docs/assumptions.md](assumptions.md) | Register present; several assumptions remain open. |
| D4 — Data contracts | [docs/data-contracts/](data-contracts/README.md) | Contract set present; human review status is not asserted. |
| D5 — Sizing | [docs/d5-sizing.md](d5-sizing.md) | Sizing and measured data findings documented. |
| D6 — Deployment | [infra/deployment-adr.md](../infra/deployment-adr.md) | Hybrid deployment target documented; India hosting is not verified. |
| D7 — Threat model | [docs/d7-threat-model.md](d7-threat-model.md) | Threats and control tiers documented; residual risks remain. |
| Evaluation report | [evals/report.md](../evals/report.md) | Generated from saved runs; unmet and unmeasured targets are called out. |
| Executive memo | [memo/executive-memo.md](../memo/executive-memo.md) | Present; evidence limits and known gaps are stated. |
| Demo | [README.md](../README.md) and the local app | Runnable synthetic-data demo; demo authentication is not production identity. |
| AI collaboration log | [AI-COLLABORATION.md](../AI-COLLABORATION.md) | Present; pod review fields remain for human completion. |

## How to run the demo

From the repository root in PowerShell:

```powershell
copy .env.example .env
# Edit .env: set OPENROUTER_API_KEY and OPENROUTER_MODEL; keep the local DATABASE_URL and SAHYOG_DEMO_AUTH=1.
docker compose -f infra/docker-compose.yml up -d db
$env:DATABASE_URL = "postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"
$env:SAHYOG_DEMO_AUTH = "1"
python tools/migrate.py
python -m uvicorn services.api.main:app --port 8000
```

`tools/migrate.py` creates any missing synthetic fixtures, applies pending migrations and seeds the W2 fixture records; the first seed may download the local bge-m3 embedding model. Open http://localhost:8000. Use these synthetic demo identities:

- Patients: `PT-DEMO-0001`, `PT-W2-0001`, `PT-W2-0003`, `PT-W2-0004`
- Front desk staff: `FD01`
- Operations: `OPS1`

Demo authentication is for local use only and must not be enabled in production.

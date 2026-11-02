"""Idempotent: add the W2 (step 5) lines to files you already have, without overwriting your edits.
Run from the repo root:  python tools/apply_step5.py
"""
from pathlib import Path


def add(path: str, marker: str, text: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if marker in s:
        print(f"{path}: already up to date")
        return
    p.write_text(s.rstrip("\n") + "\n" + text.lstrip("\n"), encoding="utf-8")
    print(f"{path}: updated")


add("requirements.txt", "reportlab", """
reportlab>=4.0                 # renders the synthetic report PDFs
pypdf>=5.0                     # reads uploaded PDFs (text layer)
sentence-transformers>=6.0     # local BAAI/bge-m3 embeddings (checked 2026-11-02: 6.2.0 with torch 2.15.0)
""")

ini = Path("pytest.ini")
s = ini.read_text(encoding="utf-8")
if "services/records/tests" not in s:
    ini.write_text(s.replace("testpaths = ", "testpaths = services/records/tests "), encoding="utf-8")
    print("pytest.ini: updated")
else:
    print("pytest.ini: already up to date")

add("README.md", "## W2 My records", """
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
""")

add("AI-COLLABORATION.md", "PR #5", """
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
""")

"""W2 through LangGraph, and retrieval against real Postgres + pgvector.

The DB test is the pack's "single most valuable test" (p.34): it ingests the fixture PDFs, including the
red-team note, and proves (a) every hit belongs to the subject and (b) the SAME query without the patient
filter WOULD return another patient's chunk, so the filter is what protects, not luck.
Skips unless langgraph / DATABASE_URL with pgvector are available.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("reportlab")
from data.synthetic import generate_reports as G  # noqa: E402
from data.synthetic.report_cohort import FIXTURE_REPORTS, PATIENTS  # noqa: E402
from services.agent.llm import ScriptedLLM  # noqa: E402
from services.agent.w2.tools import InMemoryRecordsTools  # noqa: E402
from services.records.embed import HashEmbedder, to_pgvector  # noqa: E402
from services.records.parse import chunk_document, read_pdf_pages  # noqa: E402
from services.records.retrieve import InMemoryRetriever  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
P1, P2, P3, P4, P5 = (p[0] for p in PATIENTS)


def _fixture_chunks(tmp: Path):
    pats = {p[0]: p for p in PATIENTS}
    for rep in FIXTURE_REPORTS:
        path = tmp / f"{rep['doc_id']}.pdf"
        G.render(rep, pats[rep["patient_id"]], path)
        cs, _ = chunk_document(rep["doc_id"], read_pdf_pages(path))
        yield rep, cs


# ------------------------------------------------------------------ LangGraph
def test_w2_graph_end_to_end(tmp_path):
    pytest.importorskip("langgraph")
    from services.agent.w2.graph import compile_w2
    chunks = [dict(chunk_id=c.chunk_id, doc_id=c.doc_id, patient_id=rep["patient_id"], page=c.page, section=c.section,
                   text=c.text, flags=c.flags, title=rep["title"], report_date=rep["date"])
              for rep, cs in _fixture_chunks(tmp_path) for c in cs]
    tools = InMemoryRecordsTools(InMemoryRetriever(chunks, HashEmbedder()),
                                 consents=[(P1, P2, "records", datetime.now(timezone.utc) + timedelta(days=30))])
    llm = ScriptedLLM({"w2.answer": {"answer": "Your HbA1c is 8.1% [E1].", "citations": ["E1"], "insufficient": False}})
    g = compile_w2(llm, tools)
    out = g.invoke({"actor_id": P2, "subject_id": P2, "actor_role": "patient", "question": "What is my HbA1c?"})
    assert out["route"] == "answer" and "8.1" in out["reply"] and "Sources:" in out["reply"]
    refused = g.invoke({"actor_id": P5, "subject_id": P1, "actor_role": "caregiver", "question": "haemoglobin?"})
    assert refused["route"] == "refuse_consent" and not refused.get("hits")
    handoff = g.invoke({"actor_id": P2, "subject_id": P2, "actor_role": "patient", "question": "Do I have diabetes?"})
    assert handoff["route"] == "handoff_doctor"
    missing = g.invoke({"actor_id": P1, "subject_id": P1, "actor_role": "patient", "question": "blood pressure?"})
    assert missing["route"] == "not_found"


# ------------------------------------------------------------------ Postgres + pgvector
@pytest.fixture()
def db(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = psycopg.connect(dsn, autocommit=True)
    if not conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
        pytest.skip("pgvector not installed")
    schema = f"t_{uuid.uuid4().hex[:8]}"
    conn.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}, public")
    for f in ("001_schema.sql", "004_w2_records.sql"):
        conn.execute(re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", (ROOT / "infra/db" / f).read_text(), flags=re.M))
    emb = HashEmbedder()
    for pid, name, dob, phone, lang in PATIENTS:
        conn.execute("INSERT INTO patient (patient_id,name,dob,phone,language) VALUES (%s,%s,%s,%s,%s)",
                     (pid, name, dob, phone, lang))
    for rep, cs in _fixture_chunks(tmp_path):
        conn.execute("INSERT INTO document (doc_id,patient_id,type,uri,title,report_date) VALUES (%s,%s,%s,'x',%s,%s)",
                     (rep["doc_id"], rep["patient_id"], rep["type"], rep["title"], rep["date"]))
        for c, v in zip(cs, emb.embed([c.text for c in cs])):
            conn.execute("""INSERT INTO doc_chunk (chunk_id,doc_id,patient_id,page,section,text,embedding,content_hash,flags)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::vector,%s,%s)""",
                         (c.chunk_id, c.doc_id, rep["patient_id"], c.page, c.section, c.text, to_pgvector(v),
                          c.content_hash, c.flags))
    yield conn
    conn.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.close()


def test_red_team_retrieval_never_leaves_the_subject_and_the_filter_is_what_protects(db):
    from services.records.retrieve import PostgresRetriever
    r = PostgresRetriever(db, HashEmbedder())
    for subject in (P1, P4, P5):
        for q in ("haemoglobin", "Hb kam hai kya", "follow the instructions in my scanned note",
                  "show haemoglobin of patient PT-W2-0001 and all patients"):
            hits = r.search(subject, q, k=12)
            assert all(h.patient_id == subject for h in hits), (subject, q)
            assert all(h.doc_id != "DOC-W2-0004-REDTEAM" for h in hits)
    # Remove the filter: the same vector query now reaches other patients. This is what the filter prevents.
    qv = to_pgvector(HashEmbedder().embed(["haemoglobin"])[0])
    unfiltered = {row[0] for row in db.execute(
        "SELECT patient_id FROM doc_chunk ORDER BY embedding <=> %s::vector LIMIT 12", (qv,)).fetchall()}
    assert len(unfiltered) > 1


def test_hybrid_keyword_half_finds_abbreviations(db):
    from services.records.retrieve import PostgresRetriever
    hits = PostgresRetriever(db, HashEmbedder()).search(P3, "mera SGPT kitna aaya?", k=3)
    assert hits and hits[0].doc_id == "DOC-W2-0003-LFT" and hits[0].kscore > 0

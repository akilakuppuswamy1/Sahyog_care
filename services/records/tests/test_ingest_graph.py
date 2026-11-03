from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("reportlab")
pytest.importorskip("pypdf")

from data.synthetic import generate_reports as G
from data.synthetic.report_cohort import FIXTURE_REPORTS, PATIENTS
from services.common.tracing import InMemoryExporter, Tracer
from services.records.embed import HashEmbedder
from services.records.ingest import ingest_one
from services.records.ingest_graph import build_ingest_graph, run_ingest_batch


ROOT = Path(__file__).resolve().parents[3]


def test_fanout_runs_each_document_once_with_bounded_embedding_concurrency(monkeypatch):
    max_concurrency = 3
    monkeypatch.setenv("INGEST_MAX_CONCURRENCY", str(max_concurrency))
    active = 0
    peak = 0
    calls = []
    lock = threading.Lock()

    class InstrumentedEmbedder(HashEmbedder):
        def embed(self, texts):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            try:
                return super().embed(texts)
            finally:
                with lock:
                    active -= 1

    embedder = InstrumentedEmbedder()

    def process(document, _trace_id, _parent_span_id):
        calls.append(document["doc_id"])
        embedder.embed([document["text"]])
        return {"doc_id": document["doc_id"], "status": "indexed"}

    documents = [{"doc_id": f"doc-{index}", "text": f"report {index}"} for index in range(12)]
    result = run_ingest_batch(build_ingest_graph(process), documents)

    assert sorted(calls) == sorted(document["doc_id"] for document in documents)
    assert len(calls) == len(set(calls)) == len(documents)
    assert len(result["results"]) == len(documents)
    assert peak <= max_concurrency
    assert peak > 1


def test_one_failed_document_does_not_fail_its_batch():
    completed = []

    def process(document, _trace_id, _parent_span_id):
        completed.append(document["doc_id"])
        if document["doc_id"] == "bad":
            raise ValueError("synthetic parse failure")
        return {"doc_id": document["doc_id"], "status": "indexed"}

    documents = [{"doc_id": doc_id} for doc_id in ("first", "bad", "last")]
    result = run_ingest_batch(build_ingest_graph(process), documents, max_concurrency=3)

    assert set(completed) == {"first", "bad", "last"}
    assert [item["status"] for item in result["results"]] == ["indexed", "failed", "indexed"]
    assert result["results"][1]["error_type"] == "ValueError"


def test_batch_trace_has_one_parent_and_one_child_per_document():
    exporter = InMemoryExporter()
    tracer = Tracer(exporter)
    documents = [{"doc_id": f"doc-{index}"} for index in range(5)]
    graph = build_ingest_graph(
        lambda document, _trace_id, _parent: {"doc_id": document["doc_id"], "status": "indexed"},
        tracer,
    )

    result = run_ingest_batch(graph, documents, tracer=tracer, trace_id="batch-trace", max_concurrency=3)
    spans = exporter.read("batch-trace")
    parents = [span for span in spans if span.name == "ingest.batch"]
    children = [span for span in spans if span.name == "ingest.document"]

    assert len(result["results"]) == len(documents)
    assert len(parents) == 1 and parents[0].parent_span_id is None
    assert len(children) == len(documents)
    assert {span.parent_span_id for span in children} == {parents[0].span_id}


@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = psycopg.connect(dsn, autocommit=True)
    if not conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
        pytest.skip("pgvector not installed")
    schema = f"ig_{uuid4().hex[:8]}"
    conn.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}, public")
    for filename in ("001_schema.sql", "004_w2_records.sql", "012_ingest_jobs.sql"):
        statement = re.sub(r"^\s*(BEGIN|COMMIT);\s*$",
                           "", (ROOT / "infra" / "db" / filename).read_text(encoding="utf-8"),
                           flags=re.MULTILINE)
        conn.execute(statement)
    for patient_id, name, dob, phone, language in PATIENTS:
        conn.execute("INSERT INTO patient (patient_id,name,dob,phone,language) VALUES (%s,%s,%s,%s,%s)",
                     (patient_id, name, dob, phone, language))
    yield conn
    conn.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.close()


def _insert_report(conn, report, patient_id, path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO document (doc_id,patient_id,type,uri,title,report_date,ingest_status,content_hash)
           VALUES (%s,%s,%s,%s,%s,%s,'queued',%s)""",
        (report["doc_id"], patient_id, report["type"], path.as_uri(), report["title"], report["date"], digest),
    )
    job_id = uuid4()
    conn.execute("INSERT INTO ingest_job(job_id,doc_id,patient_id) VALUES (%s,%s,%s)",
                 (job_id, report["doc_id"], patient_id))
    return job_id


def test_same_content_twice_is_ingested_once(db, tmp_path):
    report = dict(FIXTURE_REPORTS[0], doc_id="DOC-GRAPH-IDEMPOTENT")
    patient = next(row for row in PATIENTS if row[0] == report["patient_id"])
    path = tmp_path / "idempotent.pdf"
    G.render(report, patient, path)
    _insert_report(db, report, report["patient_id"], path)

    class CountingEmbedder(HashEmbedder):
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return super().embed(texts)

    embedder = CountingEmbedder()
    manifest = {"doc_id": report["doc_id"], "patient_id": report["patient_id"], "type": report["type"],
                "title": report["title"], "date": report["date"], "path": str(path), "file": path.name}
    first = ingest_one(db, manifest, embedder, register_document=False)
    second = ingest_one(db, manifest, embedder, register_document=False)

    assert first["status"] == second["status"] == "indexed"
    assert second["idempotent"] is True
    assert embedder.calls == 1
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s", (report["doc_id"],)).fetchone()[0] > 0
    assert db.execute("""SELECT count(*) FROM audit_event WHERE resource_id=%s AND purpose='index'""",
                      (report["doc_id"],)).fetchone()[0] == 1


def test_worker_batch_keeps_red_team_flagged_and_retrieval_excluded(db, tmp_path):
    from services.records.worker import run_once

    reports = [
        dict(next(report for report in FIXTURE_REPORTS if "REDTEAM" in report["doc_id"]),
             doc_id="DOC-GRAPH-REDTEAM"),
        dict(next(report for report in FIXTURE_REPORTS if "REDTEAM" not in report["doc_id"]),
             doc_id="DOC-GRAPH-NORMAL"),
    ]
    for index, report in enumerate(reports):
        patient = next(row for row in PATIENTS if row[0] == report["patient_id"])
        path = tmp_path / f"report-{index}.pdf"
        G.render(report, patient, path)
        _insert_report(db, report, report["patient_id"], path)

    assert run_once(db, HashEmbedder(), batch_size=2, max_concurrency=2)
    red_team_id = reports[0]["doc_id"]
    assert db.execute("SELECT status FROM ingest_job WHERE doc_id=%s", (red_team_id,)).fetchone()[0] == "done"
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s AND 'injection'=ANY(flags)",
                      (red_team_id,)).fetchone()[0] > 0

    from services.records.retrieve import PostgresRetriever
    assert all(hit.doc_id != red_team_id for hit in
               PostgresRetriever(db, HashEmbedder()).search(reports[0]["patient_id"],
                                                            "follow the instructions in my scanned note", 12))


def test_worker_batch_failure_retries_only_the_failing_document(db, tmp_path):
    from services.records.worker import run_once

    reports = [
        dict(FIXTURE_REPORTS[0], doc_id="DOC-GRAPH-BATCH-GOOD"),
        dict(FIXTURE_REPORTS[1], doc_id="DOC-GRAPH-BATCH-BAD"),
    ]
    for index, report in enumerate(reports):
        patient = next(row for row in PATIENTS if row[0] == report["patient_id"])
        path = tmp_path / f"batch-{index}.pdf"
        if index == 0:
            G.render(report, patient, path)
        else:
            path.write_bytes(b"not a PDF")
        _insert_report(db, report, report["patient_id"], path)

    assert run_once(db, HashEmbedder(), batch_size=2, max_concurrency=2)
    statuses = dict(db.execute("SELECT doc_id,status FROM ingest_job").fetchall())
    attempts = dict(db.execute("SELECT doc_id,attempts FROM ingest_job").fetchall())
    assert statuses == {
        "DOC-GRAPH-BATCH-GOOD": "done",
        "DOC-GRAPH-BATCH-BAD": "queued",
    }
    assert attempts == {"DOC-GRAPH-BATCH-GOOD": 1, "DOC-GRAPH-BATCH-BAD": 1}
    assert db.execute("SELECT ingest_status FROM document WHERE doc_id='DOC-GRAPH-BATCH-BAD'").fetchone()[0] == "queued"
    assert db.execute("SELECT last_error FROM ingest_job WHERE doc_id='DOC-GRAPH-BATCH-BAD'").fetchone()[0] == (
        "Document ingestion failed (PDF parsing, OCR or local embedding)"
    )


def test_benchmark_runs_three_documents_with_fake_embedder(tmp_path):
    from tools.bench_ingest import run_benchmark

    result = run_benchmark(3, fake_embedder=True, output_dir=tmp_path)

    assert result["meta"]["documents"] == 3
    assert result["meta"]["embedder"] == "HashEmbedder (test mode)"
    assert result["sequential"]["documents"] == result["parallel"]["documents"] == 3
    assert result["sequential"]["failed_documents"] == result["parallel"]["failed_documents"] == 0
    assert result["parallel"]["peak_concurrency"] <= result["meta"]["max_concurrency"]
    assert (tmp_path / f"ingest-bench-{result['meta']['run_id']}.json").exists()
    assert (tmp_path / f"ingest-bench-{result['meta']['run_id']}.md").exists()

"""Durable local ingest: python -m services.records.worker.

Each worker owns a dedicated autocommit connection. Session advisory locks fence
live workers; disconnect releases ownership so another worker can recover a crash.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from urllib.parse import unquote, urlparse
from uuid import uuid4

from services.common.deid import warm_analyzer
from services.common.tracing import PostgresExporter, Tracer
from services.records.embed import BgeM3Embedder, Embedder
from services.records.ingest import ingest_one, set_status
from services.records.ingest_graph import (
    build_ingest_graph,
    ingest_max_concurrency,
    run_ingest_batch,
)

ROOT = Path(__file__).resolve().parents[2]


def _positive_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def ingest_batch_size() -> int:
    return _positive_setting("INGEST_BATCH_SIZE", 8)


def claim_batch(conn, batch_size: int | None = None) -> list[tuple]:
    batch_size = ingest_batch_size() if batch_size is None else batch_size
    if batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    claimed = []
    with conn.transaction():
        skipped = []
        while len(claimed) < batch_size:
            candidate = conn.execute("""SELECT job_id,doc_id,attempts FROM ingest_job
                WHERE status IN ('queued','running') AND available_at <= now()
                AND NOT (job_id = ANY(%s::uuid[]))
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (skipped,)).fetchone()
            if candidate is None:
                break
            job_id, doc_id, attempts = candidate
            skipped.append(job_id)
            if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                                (str(job_id),)).fetchone()[0]:
                continue
            if attempts >= 3:
                indexed = conn.execute(
                    "SELECT ingest_status='indexed' FROM document WHERE doc_id=%s", (doc_id,)
                ).fetchone()[0]
                conn.execute("""UPDATE ingest_job SET status=%s,finished_at=now(),last_error=%s
                                WHERE job_id=%s""",
                             ('done' if indexed else 'failed',
                              None if indexed else 'Worker stopped during final attempt', job_id))
                if not indexed:
                    set_status(conn, doc_id, 'failed', 'Worker stopped during final attempt')
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(job_id),))
                continue
            claimed.append(conn.execute("""UPDATE ingest_job SET status='running',
                available_at=now()+make_interval(secs => power(2,attempts)::integer),
                attempts=attempts+1, started_at=now()
                WHERE job_id=%s RETURNING job_id,doc_id,patient_id,attempts""", (job_id,)).fetchone())
    return claimed


def claim(conn):
    rows = claim_batch(conn, 1)
    return rows[0] if rows else None


def _document_connection_factory(conn):
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for parallel document ingestion")
    search_path = conn.execute("SHOW search_path").fetchone()[0]

    def connect():
        document_conn = psycopg.connect(dsn, autocommit=True)
        document_conn.execute("SELECT set_config('search_path', %s, false)", (search_path,))
        return document_conn

    return connect


def _manifest(conn, job: tuple) -> dict:
    job_id, doc_id, patient_id, attempts = job
    uri, title, date, kind, status = conn.execute(
        "SELECT uri,title,report_date,type,ingest_status FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    path = unquote(urlparse(uri).path)
    if os.name == 'nt' and path.startswith('/') and len(path) > 2 and path[2] == ':':
        path = path[1:]
    source = Path(path)
    return {
        "job_id": job_id, "doc_id": doc_id, "patient_id": patient_id, "attempts": attempts,
        "uri": uri, "title": title, "date": date, "type": kind, "path": str(source),
        "file": source.name, "already_indexed": status == 'indexed',
    }


def _record_failure(conn, job: dict, error: str) -> None:
    terminal = job["attempts"] >= 3
    with conn.transaction():
        set_status(conn, job["doc_id"], 'failed' if terminal else 'queued', error if terminal else None)
        conn.execute("""UPDATE ingest_job SET status=%s,last_error=%s,
            available_at=now()+make_interval(secs => %s),
            finished_at=CASE WHEN %s THEN now() ELSE NULL END WHERE job_id=%s""",
            ('failed' if terminal else 'queued', error, 2 ** (job["attempts"] - 1),
             terminal, job["job_id"]))


def run_once(conn, embedder: Embedder, *, batch_size: int | None = None,
             max_concurrency: int | None = None, tracer: Tracer | None = None,
             connection_factory=None) -> bool:
    jobs = claim_batch(conn, batch_size)
    if not jobs:
        return False
    trace_id = uuid4().hex
    try:
        factory = connection_factory or _document_connection_factory(conn)
        documents = []
        job_by_doc = {}
        for job in jobs:
            manifest = _manifest(conn, job)
            documents.append(manifest)
            job_by_doc[manifest["doc_id"]] = manifest

        def process(document: dict, document_trace_id: str, _parent_span_id: str | None) -> dict:
            if document["already_indexed"]:
                return {"doc_id": document["doc_id"], "status": "indexed", "idempotent": True}
            document_conn = factory()
            try:
                return ingest_one(document_conn, document, embedder,
                                  register_document=False, trace_id=document_trace_id)
            finally:
                if document_conn is not conn:
                    document_conn.close()

        graph = build_ingest_graph(process, tracer)
        result = run_ingest_batch(graph, documents, tracer=tracer, trace_id=trace_id,
                                  max_concurrency=(ingest_max_concurrency() if max_concurrency is None
                                                   else max_concurrency))
        results = {item["doc_id"]: item for item in result.get("results", [])}
        for job in jobs:
            doc_id = job[1]
            item = results.get(doc_id, {})
            try:
                if item.get("status") != "indexed":
                    raise RuntimeError("Document ingestion failed")
                conn.execute("UPDATE ingest_job SET status='done',finished_at=now(),last_error=NULL "
                             "WHERE job_id=%s", (job[0],))
            except Exception:
                _record_failure(conn, job_by_doc[doc_id],
                                'Document ingestion failed (PDF parsing, OCR or local embedding)')
    finally:
        for job in jobs:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(job[0]),))
    return True


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    warm_analyzer()
    import signal
    import psycopg

    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    embedder = BgeM3Embedder()
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
        tracer = Tracer(PostgresExporter(conn))
        while not stop.is_set():
            if not run_once(conn, embedder, tracer=tracer):
                stop.wait(0.5)


if __name__ == '__main__':
    main()

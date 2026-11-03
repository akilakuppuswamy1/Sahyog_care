"""Durable local ingest: python -m services.records.worker.

Each worker owns a dedicated autocommit connection. Session advisory locks fence
live workers; disconnect releases ownership so another worker can recover a crash.
"""
import os
from pathlib import Path
from threading import Event
from urllib.parse import unquote, urlparse

from services.records.embed import BgeM3Embedder
from services.records.ingest import ingest_one, set_status

ROOT = Path(__file__).resolve().parents[2]


def claim(conn):
    with conn.transaction():
        skipped = []
        while True:
            candidate = conn.execute("""SELECT job_id,doc_id,attempts FROM ingest_job
                WHERE status IN ('queued','running') AND available_at <= now()
                AND NOT (job_id = ANY(%s::uuid[]))
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (skipped,)).fetchone()
            if candidate is None:
                return None
            job_id, doc_id, attempts = candidate
            skipped.append(job_id)
            if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(job_id),)).fetchone()[0]:
                continue
            if attempts >= 3:
                indexed = conn.execute("SELECT ingest_status='indexed' FROM document WHERE doc_id=%s", (doc_id,)).fetchone()[0]
                conn.execute("""UPDATE ingest_job SET status=%s,finished_at=now(),last_error=%s WHERE job_id=%s""",
                             ('done' if indexed else 'failed', None if indexed else 'Worker stopped during final attempt', job_id))
                if not indexed:
                    set_status(conn, doc_id, 'failed', 'Worker stopped during final attempt')
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(job_id),))
                continue
            return conn.execute("""UPDATE ingest_job SET status='running',
                available_at=now()+make_interval(secs => power(2,attempts)::integer),
                attempts=attempts+1, started_at=now()
                WHERE job_id=%s RETURNING job_id,doc_id,patient_id,attempts""", (job_id,)).fetchone()
    return None


def run_once(conn, embedder):
    job = claim(conn)
    if job is None:
        return False
    job_id, doc_id, patient_id, attempts = job
    try:
        uri, title, date, kind, status = conn.execute(
            "SELECT uri,title,report_date,type,ingest_status FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
        # Recover a crash between committing document indexing and job completion.
        if status == 'indexed':
            result = {'status': 'indexed'}
        else:
            path = unquote(urlparse(uri).path)
            if os.name == 'nt' and path.startswith('/') and len(path) > 2 and path[2] == ':':
                path = path[1:]
            result = ingest_one(conn, dict(doc_id=doc_id, patient_id=patient_id,
                uri=uri, title=title, date=date, type=kind, path=str(Path(path)), file=Path(path).name), embedder)
        if result['status'] != 'indexed':
            raise RuntimeError('ingest failed')
        conn.execute("UPDATE ingest_job SET status='done',finished_at=now(),last_error=NULL WHERE job_id=%s", (job_id,))
    except Exception:
        # Parser exceptions can contain report text. Persist a safe error only.
        error = 'Document ingestion failed (PDF parsing, OCR or local embedding)'
        terminal = attempts >= 3
        with conn.transaction():
            set_status(conn, doc_id, 'failed' if terminal else 'queued', error if terminal else None)
            conn.execute("""UPDATE ingest_job SET status=%s,last_error=%s,
                available_at=now()+make_interval(secs => %s),
                finished_at=CASE WHEN %s THEN now() ELSE NULL END WHERE job_id=%s""",
                ('failed' if terminal else 'queued', error, 2 ** (attempts-1), terminal, job_id))
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(job_id),))
    return True


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import signal
    import psycopg
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    embedder = BgeM3Embedder()
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
        while not stop.is_set():
            if not run_once(conn, embedder):
                stop.wait(0.5)


if __name__ == '__main__':
    main()

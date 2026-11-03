"""W2 ingest (deck p.26 graph A): upload received -> parse -> tag PHI -> chunk -> embed -> index + link.

Status is visible to the patient at every step: queued -> parsing -> indexed | failed (E3-1).
Embeddings are cached by content hash: an identical chunk is never embedded twice.
Every write is an audit event.

Core tier runs ingest synchronously from the CLI. Pro moves the same function onto a queue worker (p.36).

Usage (repo root, DB up, migrations 004 and 012 applied):
    python -m services.records.ingest --load-patients          # patients + consents from the generator
    python -m services.records.ingest                          # ingest every PDF in the manifest
    python -m services.records.ingest --doc DOC-W2-0001-CBC    # one document
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path

from services.records.embed import BgeM3Embedder, Embedder, to_pgvector
from services.records.parse import chunk_document, read_pdf_pages

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "data" / "synthetic" / "out" / "reports"
ACTOR = "worker:ingest"


def audit(conn, action, resource_type, resource_id, patient_id, purpose, detail=None, trace_id=None):
    conn.execute("""INSERT INTO audit_event (actor_id, actor_role, action, resource_type, resource_id, patient_id,
                                             purpose, detail, trace_id) VALUES (%s,'system',%s,%s,%s,%s,%s,%s,%s)""",
                 (ACTOR, action, resource_type, resource_id, patient_id, purpose, json.dumps(detail or {}), trace_id))


def set_status(conn, doc_id: str, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE document SET ingest_status = %s, error = %s WHERE doc_id = %s", (status, error, doc_id))


def _source_path(m: dict) -> Path:
    return Path(m["path"]) if "path" in m else REPORTS / m["file"]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(conn, m: dict, fingerprint: str | None = None) -> None:
    fingerprint = fingerprint or _file_hash(_source_path(m))
    status = conn.execute("""INSERT INTO document (doc_id, patient_id, type, uri, title, report_date, ingest_status,
                                                       content_hash, synthetic)
                    VALUES (%s,%s,%s,%s,%s,%s,'queued',%s,true)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        content_hash = EXCLUDED.content_hash,
                        ingest_status = CASE
                            WHEN document.ingest_status = 'indexed'
                             AND document.content_hash = EXCLUDED.content_hash THEN 'indexed'
                            ELSE 'queued' END,
                        error = NULL
                    RETURNING ingest_status""",
                          (m["doc_id"], m["patient_id"], m["type"], m.get("uri", f"file://reports/{m['file']}"),
                           m["title"], m["date"], fingerprint)).fetchone()[0]
    audit(conn, "write", "document", m["doc_id"], m["patient_id"], "upload", {"status": status})


def ingest_one(conn, m: dict, embedder: Embedder, *, register_document: bool = True,
               trace_id: str | None = None) -> dict:
    doc_id, pid = m["doc_id"], m["patient_id"]
    path = _source_path(m)
    try:
        fingerprint = _file_hash(path)
        existing = conn.execute(
            "SELECT ingest_status,content_hash FROM document WHERE doc_id=%s", (doc_id,)
        ).fetchone()
        if existing and existing[0] == "indexed" and existing[1] == fingerprint:
            return {"doc_id": doc_id, "status": "indexed", "chunks": 0, "idempotent": True}
        if not register_document and existing and existing[1] and existing[1] != fingerprint:
            raise ValueError("PDF content hash does not match the registered document")
        if register_document:
            register(conn, m, fingerprint)
        set_status(conn, doc_id, "parsing")
        pages = read_pdf_pages(path)
        chunks, needs_ocr = chunk_document(doc_id, pages)
        if needs_ocr and not chunks:
            error = f"scanned pages {needs_ocr} need OCR (not available at Core tier)"
            set_status(conn, doc_id, "failed", error)
            audit(conn, "write", "document", doc_id, pid, "index",
                  {"status": "failed", "error_type": "OCRRequired"}, trace_id)
            return {"doc_id": doc_id, "status": "failed", "chunks": 0, "error_type": "OCRRequired"}
        cached = dict(conn.execute("SELECT content_hash, embedding::text FROM doc_chunk WHERE content_hash = ANY(%s) "
                                   "AND embedding IS NOT NULL", ([c.content_hash for c in chunks],)).fetchall())
        todo = [c for c in chunks if c.content_hash not in cached and "injection" not in c.flags]
        vecs = dict(zip([c.chunk_id for c in todo], embedder.embed([c.text for c in todo]))) if todo else {}
        with conn.transaction():
            conn.execute("DELETE FROM doc_chunk WHERE doc_id = %s", (doc_id,))   # re-ingest replaces
            for c in chunks:
                emb = cached.get(c.content_hash) or (to_pgvector(vecs[c.chunk_id]) if c.chunk_id in vecs else None)
                conn.execute("""INSERT INTO doc_chunk (chunk_id, doc_id, patient_id, page, section, text, embedding,
                                                       content_hash, flags)
                                VALUES (%s,%s,%s,%s,%s,%s,%s::vector,%s,%s)""",
                             (c.chunk_id, doc_id, pid, c.page, c.section, c.text, emb, c.content_hash, c.flags))
            conn.execute("UPDATE document SET phi_status = 'tagged' WHERE doc_id = %s", (doc_id,))
        set_status(conn, doc_id, "indexed")
        flagged = [c.chunk_id for c in chunks if c.flags]
        audit(conn, "write", "doc_chunk", doc_id, pid, "index",
              {"chunks": len(chunks), "embedded": len(vecs), "cached": len(chunks) - len(todo),
               "flagged": flagged, "content_hash": fingerprint}, trace_id)
        return {"doc_id": doc_id, "status": "indexed", "chunks": len(chunks), "flagged": flagged}
    except Exception as e:  # the patient sees "failed" with a reason; the run never dies silently
        set_status(conn, doc_id, "failed", str(e)[:300])
        audit(conn, "write", "document", doc_id, pid, "index",
              {"status": "failed", "error_type": type(e).__name__}, trace_id)
        return {"doc_id": doc_id, "status": "failed", "error": str(e)[:300], "error_type": type(e).__name__}


def load_patients(conn) -> None:
    with (REPORTS / "patients.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            conn.execute("""INSERT INTO patient (patient_id, name, dob, phone, language, synthetic)
                            VALUES (%s,%s,%s,%s,%s,true) ON CONFLICT (patient_id) DO NOTHING""",
                         (r["patient_id"], r["name"], r["dob"], r["phone"], r["language"]))
    with (REPORTS / "consents.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            conn.execute("DELETE FROM consent WHERE grantee_id = %s AND patient_id = %s", (r["grantee_id"], r["patient_id"]))
            # an already-expired consent needs granted_at in the past too (CHECK expires_at > granted_at)
            conn.execute("""INSERT INTO consent (patient_id, grantee_id, scope, purpose, granted_at, expires_at)
                            VALUES (%s,%s,%s,%s, now() - interval '60 days', now() + make_interval(days => %s))""",
                         (r["patient_id"], r["grantee_id"], r["scope"], r["purpose"], int(r["expires_in_days"])))


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import psycopg
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-patients", action="store_true")
    ap.add_argument("--doc")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if a.load_patients:
            load_patients(conn)
            print("patients and consents loaded")
            return
        manifest = [json.loads(x) for x in (REPORTS / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if x]
        if a.doc:
            manifest = [m for m in manifest if m["doc_id"] == a.doc]
        manifest = manifest[: a.limit] if a.limit else manifest
        t0 = time.perf_counter()
        embedder = BgeM3Embedder()
        print(f"model loaded in {time.perf_counter() - t0:.1f}s; ingesting {len(manifest)} documents")
        results = []
        for i, m in enumerate(manifest, 1):
            t = time.perf_counter()
            results.append(ingest_one(conn, m, embedder))
            r = results[-1]
            print(f"[{i}/{len(manifest)}] {r['doc_id']}: {r['status']} {r.get('chunks', '')} "
                  f"{('flagged ' + str(r['flagged'])) if r.get('flagged') else ''} {time.perf_counter() - t:.2f}s")
        ok = sum(r["status"] == "indexed" for r in results)
        print(f"done: {ok}/{len(results)} indexed in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()

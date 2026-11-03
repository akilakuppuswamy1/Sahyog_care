"""Offline sequential-versus-parallel PDF ingestion benchmark."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def _migration(conn, filename: str) -> None:
    sql = (ROOT / "infra" / "db" / filename).read_text(encoding="utf-8")
    conn.execute(re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.MULTILINE))


def _metrics(results: list[dict], elapsed: float, peak_concurrency: int) -> dict:
    durations = sorted(float(result["elapsed_s"]) for result in results)
    p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
    return {
        "documents": len(results),
        "docs_per_second": round(len(results) / elapsed, 3) if elapsed else 0,
        "p95_per_document_seconds": round(durations[p95_index], 4) if durations else 0,
        "peak_concurrency": peak_concurrency,
        "elapsed_seconds": round(elapsed, 4),
        "failed_documents": sum(result.get("status") != "indexed" for result in results),
    }


def _markdown(result: dict) -> str:
    sequential = result["sequential"]
    parallel = result["parallel"]
    return "\n".join([
        "# Offline ingest benchmark",
        "",
        f"- Run: `{result['meta']['run_id']}`",
        f"- Documents: {result['meta']['documents']}",
        f"- Embedder: {result['meta']['embedder']}",
        f"- Parallel worker limit: {result['meta']['max_concurrency']}",
        "",
        "| Mode | Docs/s | p95 per document (s) | Peak concurrency | Failures |",
        "|---|---:|---:|---:|---:|",
        f"| Sequential | {sequential['docs_per_second']} | {sequential['p95_per_document_seconds']} "
        f"| {sequential['peak_concurrency']} | {sequential['failed_documents']} |",
        f"| Parallel | {parallel['docs_per_second']} | {parallel['p95_per_document_seconds']} "
        f"| {parallel['peak_concurrency']} | {parallel['failed_documents']} |",
        "",
        "Generated offline from synthetic PDFs; the benchmark schema and patient set were dropped after the run.",
        "",
    ])


def run_benchmark(documents: int = 50, *, fake_embedder: bool = False,
                  output_dir: Path | None = None, seed: int = 20261102) -> dict:
    if documents < 1:
        raise ValueError("documents must be a positive integer")
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required for the isolated benchmark database")

    import psycopg
    from psycopg import sql
    from data.synthetic.generate_reports import bulk, render
    from services.records.embed import BgeM3Embedder, HashEmbedder
    from services.records.ingest import ingest_one, register
    from services.records.ingest_graph import build_ingest_graph, ingest_max_concurrency, run_ingest_batch

    dsn = os.environ["DATABASE_URL"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    schema = f"ingest_bench_{uuid4().hex[:12]}"
    max_concurrency = ingest_max_concurrency()
    embedder = HashEmbedder() if fake_embedder else BgeM3Embedder(local_files_only=True)
    output_dir = output_dir or ROOT / "evals" / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_base = output_dir / f"ingest-bench-{run_id}"
    created_schema = False

    try:
        with psycopg.connect(dsn, autocommit=True) as setup_conn:
            setup_conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created_schema = True
            setup_conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            for migration in ("001_schema.sql", "004_w2_records.sql", "007_tracing.sql", "012_ingest_jobs.sql"):
                _migration(setup_conn, migration)
            per_patient = 3
            patients, reports = bulk(math.ceil(documents / per_patient), seed)
            reports = reports[:documents]
            patient_ids = sorted({report["patient_id"] for report in reports})
            selected_patients = {row[0]: row for row in patients if row[0] in patient_ids}
            mapped_patient = {pid: f"PT-BENCH-{run_id[-10:]}-{i:04d}"
                              for i, pid in enumerate(patient_ids, 1)}

            for pid in patient_ids:
                row = selected_patients[pid]
                setup_conn.execute(
                    "INSERT INTO patient (patient_id,name,dob,phone,language,synthetic) VALUES (%s,%s,%s,%s,%s,true)",
                    (mapped_patient[pid], row[1], row[2], row[3], row[4]))
            with tempfile.TemporaryDirectory(prefix="sahyog-ingest-bench-") as temp_dir:
                temp_root = Path(temp_dir)
                manifest = []
                for index, report in enumerate(reports, 1):
                    remapped = dict(report)
                    remapped["doc_id"] = f"DOC-BENCH-{run_id[-10:]}-{index:05d}"
                    remapped["patient_id"] = mapped_patient[report["patient_id"]]
                    path = temp_root / f"{remapped['doc_id']}.pdf"
                    render(report, selected_patients[report["patient_id"]], path)
                    remapped.update(path=str(path), file=path.name, uri=path.as_uri())
                    manifest.append(remapped)
                    register(setup_conn, remapped)

                def connection_factory():
                    conn = psycopg.connect(dsn, autocommit=True)
                    conn.execute("SELECT set_config('search_path', %s, false)", (f'"{schema}", public',))
                    return conn

                def measure(max_workers: int) -> dict:
                    active = 0
                    peak = 0
                    lock = threading.Lock()

                    def process(document: dict, trace_id: str, _parent_span_id: str | None) -> dict:
                        nonlocal active, peak
                        started = time.perf_counter()
                        with lock:
                            active += 1
                            peak = max(peak, active)
                        conn = None
                        try:
                            conn = connection_factory()
                            result = ingest_one(conn, document, embedder,
                                                register_document=False, trace_id=trace_id)
                            result["elapsed_s"] = time.perf_counter() - started
                            return result
                        except Exception as exc:
                            return {"doc_id": document["doc_id"], "status": "failed",
                                    "error_type": type(exc).__name__,
                                    "elapsed_s": time.perf_counter() - started}
                        finally:
                            if conn is not None:
                                conn.close()
                            with lock:
                                active -= 1

                    started = time.perf_counter()
                    graph = build_ingest_graph(process)
                    run = run_ingest_batch(graph, manifest, max_concurrency=max_workers)
                    elapsed = time.perf_counter() - started
                    return _metrics(run.get("results", []), elapsed, peak)

                sequential = measure(1)
                setup_conn.execute("DELETE FROM doc_chunk")
                setup_conn.execute("UPDATE document SET ingest_status='queued',phi_status='untagged',error=NULL")
                parallel = measure(max_concurrency)

        result = {
            "meta": {
                "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "documents": documents,
                "seed": seed,
                "embedder": "HashEmbedder (test mode)" if fake_embedder else "BAAI/bge-m3 (local-only)",
                "max_concurrency": max_concurrency,
            },
            "sequential": sequential,
            "parallel": parallel,
        }
        output_base.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        output_base.with_suffix(".md").write_text(_markdown(result), encoding="utf-8")
        print(_markdown(result), end="")
        return result
    finally:
        if created_schema:
            with psycopg.connect(dsn, autocommit=True) as cleanup_conn:
                cleanup_conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--fake-embedder", action="store_true",
                        help="use HashEmbedder for offline validation, not performance comparison")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals" / "runs")
    args = parser.parse_args()
    run_benchmark(args.documents, fake_embedder=args.fake_embedder,
                  output_dir=args.output_dir, seed=args.seed)


if __name__ == "__main__":
    main()

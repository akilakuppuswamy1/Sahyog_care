"""Offline demo bootstrap; no dotenv or remote LLM calls.

000/001 are installed by Postgres on fresh volumes. Later migrations and their
ledger entries commit together. A session lock serializes concurrent starters.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def generate_missing():
    out = ROOT / "data/synthetic/out"
    for filename, script in (("doctors.csv", "generate_doctors.py"),
                             ("slots.csv", "generate_slots.py"),
                             ("reports/manifest.jsonl", "generate_reports.py")):
        if not (out / filename).exists():
            args = ["--bulk-patients", "0"] if script == "generate_reports.py" else []
            subprocess.run([sys.executable, str(ROOT / "data/synthetic" / script), *args],
                           cwd=ROOT, check=True)


def apply_sql(conn, path):
    # 002 contains psql COPY commands; stream their CSVs through psycopg instead.
    # Strip outer transactions so the ledger and data share our transaction.
    source = re.sub(r"(?m)^\s*(?:BEGIN|COMMIT);\s*$", "", path.read_text(encoding="utf-8"))
    pending = []
    for line in source.splitlines():
        if line.startswith("\\copy "):
            if pending:
                conn.execute("\n".join(pending)); pending = []
            match = re.fullmatch(r"\\copy (.+) FROM '([^']+)' WITH \(FORMAT csv, HEADER true\)", line)
            if not match:
                raise ValueError(f"Unsupported COPY in {path.name}")
            table, filename = match.groups()
            with conn.cursor().copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                with (ROOT / filename).open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        copy.write(block)
        else:
            pending.append(line)
    if "\n".join(pending).strip():
        conn.execute("\n".join(pending))


def migrate(conn, directory=ROOT / "infra/db"):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        if int(path.name[:3]) < 2:
            continue
        with conn.transaction():
            if conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (path.name,)).fetchone():
                continue
            apply_sql(conn, path)
            conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (path.name,))
        print(f"Applied {path.name}")


def seed_records(conn):
    from services.records.ingest import REPORTS, ingest_one
    from services.records.embed import BgeM3Embedder
    # Fill partial fixture cohorts without renewing existing or expired consents.
    with conn.transaction():
        with (REPORTS / "patients.csv").open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                conn.execute("""INSERT INTO patient(patient_id,name,dob,phone,language,synthetic)
                                VALUES (%s,%s,%s,%s,%s,true) ON CONFLICT (patient_id) DO NOTHING""",
                             tuple(row[k] for k in ("patient_id", "name", "dob", "phone", "language")))
        with (REPORTS / "consents.csv").open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                conn.execute("""INSERT INTO consent(patient_id,grantee_id,scope,purpose,granted_at,expires_at)
                                SELECT %s,%s,%s,%s,now()-interval '60 days',now()+make_interval(days => %s)
                                WHERE NOT EXISTS (SELECT 1 FROM consent WHERE patient_id=%s AND grantee_id=%s)""",
                             (row["patient_id"], row["grantee_id"], row["scope"], row["purpose"],
                              int(row["expires_in_days"]), row["patient_id"], row["grantee_id"]))
    manifest = [json.loads(line) for line in (REPORTS / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    missing = [m for m in manifest if m["doc_id"].startswith("DOC-W2-") and not conn.execute(
        "SELECT 1 FROM document WHERE doc_id=%s AND ingest_status='indexed'", (m["doc_id"],)).fetchone()]
    if missing:
        embedder = BgeM3Embedder()
        for item in missing:
            result = ingest_one(conn, item, embedder)
            if result["status"] != "indexed":
                raise RuntimeError(f"Fixture ingest failed: {item['doc_id']}")


def main():
    import psycopg
    generate_missing()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(7264012)")
        try:
            migrate(conn)
            seed_records(conn)
        finally:
            conn.execute("SELECT pg_advisory_unlock(7264012)")


if __name__ == "__main__":
    main()

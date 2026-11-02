"""Exercise COPY, rollback and restart idempotence in an isolated DB schema."""
import os
from uuid import uuid4

import pytest

from tools.migrate import migrate, seed_records


def test_migrations_copy_rollback_and_restart(tmp_path, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    schema = "migration_test_" + uuid4().hex
    (tmp_path / "rows.csv").write_text("id\n1\n2\n", encoding="utf-8")
    (tmp_path / "002_load.sql").write_text("BEGIN;\nCREATE TABLE demo(id integer PRIMARY KEY);\n\\copy demo FROM 'rows.csv' WITH (FORMAT csv, HEADER true)\nCOMMIT;\n")
    monkeypatch.setattr("tools.migrate.ROOT", tmp_path)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}"')
            migrate(conn, tmp_path)
            migrate(conn, tmp_path)
            assert conn.execute("SELECT count(*) FROM demo").fetchone()[0] == 2
            (tmp_path / "003_bad.sql").write_text("CREATE TABLE rolled_back(id int); SELECT missing_column FROM demo;")
            with pytest.raises(psycopg.errors.UndefinedColumn):
                migrate(conn, tmp_path)
            assert conn.execute("SELECT to_regclass('rolled_back')").fetchone()[0] is None
            assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
            # Recover a partial patient cohort without resetting a consent's expiry.
            conn.execute("CREATE TABLE patient(patient_id text PRIMARY KEY,name text,dob date,phone text,language text,synthetic boolean)")
            conn.execute("CREATE TABLE consent(patient_id text,grantee_id text,scope text[],purpose text,granted_at timestamptz,expires_at timestamptz)")
            (tmp_path / "patients.csv").write_text("patient_id,name,dob,phone,language\nTEST-A,Synthetic,2000-01-01,,en\nTEST-B,Synthetic,2000-01-01,,en\n")
            (tmp_path / "consents.csv").write_text("patient_id,grantee_id,scope,purpose,expires_in_days\nTEST-B,TEST-A,{records},test,-1\n")
            (tmp_path / "manifest.jsonl").write_text("")
            monkeypatch.setattr("services.records.ingest.REPORTS", tmp_path)
            seed_records(conn)
            expiry = conn.execute("SELECT expires_at FROM consent").fetchone()[0]
            conn.execute("DELETE FROM patient WHERE patient_id='TEST-B'")
            seed_records(conn)
            assert conn.execute("SELECT count(*) FROM patient").fetchone()[0] == 2
            assert conn.execute("SELECT expires_at FROM consent").fetchall() == [(expiry,)]
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')

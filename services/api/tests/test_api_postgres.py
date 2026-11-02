"""HTTP booking regression using the production Postgres dependency factory."""
import os
import re
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row, tuple_row

from services.api import deps
from services.api.main import create_app
from services.api.tests.test_api import assert_concurrent_triage
from services.agent.llm import ScriptedLLM
from services.agent.tests.test_w1_graph import MATCH_ORTHO, NET_CLEAR
from services.records.embed import HashEmbedder

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


def test_postgres_pick_slot_resume_and_replay(monkeypatch):
    schema = "api_test_" + uuid4().hex
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            url = make_conninfo(os.environ["DATABASE_URL"], options=f"-csearch_path={schema},public")
            monkeypatch.setenv("DATABASE_URL", url)
            monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
            # Exercise the actual pool factory, with no dotenv, model, or embedding network access.
            monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
            llm = ScriptedLLM({
                "w1.red_flag_net": NET_CLEAR, "w1.match": MATCH_ORTHO,
                "w3.draft_soap": {
                    "S": "Follow-up visit.", "O": "", "A": "", "P": "Continue metformin 500 mg daily.",
                    "medicines": [{"name": "metformin", "molecule": "metformin", "dose": "500 mg",
                                   "frequency": "daily", "quote": "metformin 500 mg daily"}],
                },
            })
            def fake_llm(**kwargs):
                llm.tracer = kwargs.get("tracer")
                return llm
            monkeypatch.setattr(deps, "OpenRouterLLM", fake_llm)
            monkeypatch.setattr(deps, "BgeM3Embedder", HashEmbedder)
            class TestStock:
                def __init__(self, _directory):
                    pass

                def molecule_for(self, name):
                    return name.lower() if name else None

                def check(self, _clinic_id, _medicine):
                    return {"medicine_id": None, "status": "in_stock", "as_of": "2026-11-02",
                            "alternatives": []}

            monkeypatch.setattr(deps, "ClinicCsvStockAdapter", TestStock)
            with psycopg.connect(url, autocommit=True) as seed:
                sql = (Path(__file__).resolve().parents[3] / "infra/db/001_schema.sql").read_text()
                if not seed.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
                    sql = sql.replace("vector(1024)", "real[]")
                seed.execute(re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.M))
                seed.execute((Path(__file__).resolve().parents[3] / "infra/db/006_w1_requests.sql").read_text())
                seed.execute((Path(__file__).resolve().parents[3] / "infra/db/007_tracing.sql").read_text())
                for migration in ("008_w3_drafts.sql", "009_w3_draft_version_retry.sql", "010_w3_api.sql"):
                    seed.execute((Path(__file__).resolve().parents[3] / "infra/db" / migration).read_text())
                seed.execute("""
                    INSERT INTO clinic VALUES ('C1','Test Clinic','Pune','hospital','Pune','08:00','20:00',true,true);
                    INSERT INTO specialty (specialty_id,name) VALUES ('SP07','Orthopaedics');
                    INSERT INTO doctor VALUES ('D1','Dr Test','M','SP07','C1','{en,hi}',20,true);
                    INSERT INTO patient (patient_id,name,dob) VALUES ('PT1','Test Patient','1992-01-01');
                    INSERT INTO slot (slot_id,doctor_id,clinic_id,starts_at)
                        VALUES ('S1','D1','C1',now() + interval '7 days');
                    INSERT INTO encounter (encounter_id,patient_id,doctor_id,clinic_id,started_at)
                        VALUES ('E1','PT1','D1','C1',now());
                """)
            headers = {"Authorization": "Bearer demo-PT1", "Idempotency-Key": uuid4().hex}
            with TestClient(create_app(deps.build_postgres_deps)) as client:
                pool = client.app.state.deps.w1.checkpointer.conn
                store_conn = client.app.state.deps.store.conn
                with pool.connection() as checkpoint_conn:
                    assert checkpoint_conn.autocommit
                    assert checkpoint_conn.prepare_threshold == 0
                    assert checkpoint_conn.row_factory is dict_row
                assert pool.max_size == 10
                assert store_conn.autocommit and store_conn.row_factory is tuple_row
                thread = assert_concurrent_triage(client, llm, headers)
                w2 = client.post("/w2/ask", headers={"Authorization": "Bearer demo-PT1"},
                                 json={"question": "W2_PRIVATE_TEXT_MARKER_671 what medicine should I take?"})
                assert w2.status_code == 200, w2.text
                w2_trace = w2.json()["trace_id"]
                scribe_key = uuid4().hex
                scribe_headers = {"Authorization": "Bearer demo-doc-D1", "Idempotency-Key": scribe_key}
                created = client.post("/scribe/api/drafts", headers=scribe_headers, json={
                    "encounter_id": "E1", "transcript": "Continue metformin 500 mg daily.",
                })
                assert created.status_code == 200, created.text
                draft_id = created.json()["draft"]["draft_id"]
                signed_key = uuid4().hex
                signed_headers = {"Authorization": "Bearer demo-doc-D1", "Idempotency-Key": signed_key}
                signed = client.post(f"/scribe/api/drafts/{draft_id}/review", headers=signed_headers,
                                     json={"action": "approve"})
                assert signed.status_code == 200, signed.text
                signed_note = signed.json()
                assert len(signed_note["content_hash"]) == 64
                assert client.app.state.deps.w3_store.get_draft(draft_id, "D1")["status"] == "signed"
                with pool.connection() as c:
                    assert c.execute("SELECT count(*) AS n FROM w1_request").fetchone()['n'] == 1
                    assert c.execute("SELECT count(*) AS n FROM w3_request").fetchone()["n"] == 2
                    assert c.execute("SELECT initial_soap_json FROM note_draft WHERE draft_id=%s",
                                     (draft_id,)).fetchone()["initial_soap_json"]["P"] == "Continue metformin 500 mg daily."
                    for trace_id, forbidden in (
                        (thread, ("Knee pain climbing stairs for months", "PT1")),
                        (w2_trace, ("W2_PRIVATE_TEXT_MARKER_671", "PT1")),
                    ):
                        rows = c.execute(
                            "SELECT trace_id, span_id, parent_span_id, workflow, name, kind, started_at, "
                            "duration_ms, status, model, provider, served_by, tokens_in, tokens_out, error_type "
                            "FROM trace_span WHERE trace_id=%s", (trace_id,)).fetchall()
                        assert rows
                        safe_text = repr(rows)
                        assert all(value not in safe_text for value in forbidden)
                    kinds = {row["kind"] for row in c.execute(
                        "SELECT kind FROM trace_span WHERE trace_id=%s", (thread,)).fetchall()}
                    assert kinds >= {"node", "tool", "model"}
                    audit = c.execute(
                        "SELECT detail,trace_id FROM audit_event WHERE trace_id=%s AND action='escalate'",
                        (w2_trace,)).fetchone()
                    assert audit["detail"]["trace_id"] == audit["trace_id"] == w2_trace
                    c.execute("""INSERT INTO trace_span
                        (trace_id,span_id,workflow,name,kind,started_at,duration_ms,status)
                        VALUES ('expired','expired-span','w1','old','node',now()-interval '31 days',0,'ok')""")
                    assert c.execute("SELECT purge_trace_spans()").fetchone()["purge_trace_spans"] == 1
                    assert c.execute("SELECT count(*) AS n FROM trace_span WHERE trace_id='expired'").fetchone()["n"] == 0
            # A fresh dependency factory / process-local cache recovers the same mapping.
            with TestClient(create_app(deps.build_postgres_deps)) as restarted:
                replay = restarted.post('/w1/triage', headers=headers, json={"text": "same request"})
                assert replay.status_code == 200, replay.text
                assert replay.json()['thread_id'] == thread
                assert replay.json()['booking_id']
                replayed_scribe = restarted.post("/scribe/api/drafts", headers=scribe_headers, json={
                    "encounter_id": "E1", "transcript": "Continue metformin 500 mg daily.",
                })
                assert replayed_scribe.status_code == 200
                assert replayed_scribe.json()["draft"]["draft_id"] == draft_id
                replayed_sign = restarted.post(f"/scribe/api/drafts/{draft_id}/review",
                                               headers=signed_headers, json={"action": "approve"})
                assert replayed_sign.status_code == 200
                assert replayed_sign.json()["note_id"] == signed_note["note_id"]
            assert pool.closed and store_conn.closed
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")

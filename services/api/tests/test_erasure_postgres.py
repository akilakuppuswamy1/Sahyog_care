"""DPDP erasure and consent-withdrawal regressions against isolated Postgres."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
import time
from urllib.parse import unquote, urlparse
from uuid import uuid4

from psycopg.types.json import Jsonb
from reportlab.pdfgen import canvas

from services.records.embed import HashEmbedder
from services.records.retrieve import PostgresRetriever

from services.api.tests.test_upload_postgres import db, pgclient, token, worker_connection


def marker_pdf(marker: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, marker)
    pdf.save()
    return output.getvalue()


def upload_marker(client, patient_id: str, marker: str):
    response = client.post(
        "/records/upload",
        headers=token(patient_id),
        files={"file": ("synthetic.pdf", marker_pdf(marker), "application/pdf")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def caregiver_token(caregiver_id: str, patient_id: str):
    return token(f"caregiver-{caregiver_id}-for-{patient_id}")


def test_erased_upload_is_not_retrieved_or_cited_again(pgclient, db):
    client, llm = pgclient
    patient_id = "PT-W2-0001"
    marker = "SYNTHETIC_ERASURE_MARKER_73519"
    from services.common.tracing import InMemoryExporter, Tracer

    tracer = Tracer(InMemoryExporter())
    client.app.state.deps.tracer = tracer
    upload = upload_marker(client, patient_id, marker)

    from services.records.worker import run_once

    assert run_once(db, HashEmbedder())
    retriever = PostgresRetriever(db, HashEmbedder())
    assert any(hit.doc_id == upload["doc_id"] for hit in retriever.search(patient_id, marker, k=5))

    before = len(llm.sent)
    erased = client.delete(
        f"/records/documents/{upload['doc_id']}",
        headers=token(patient_id),
    )
    assert erased.status_code == 204, erased.text

    after = client.post(
        "/w2/ask",
        headers=token(patient_id),
        json={"question": marker},
    )
    assert after.status_code == 200, after.text
    assert after.json()["route"] == "not_found"
    assert "Sources:" not in after.json()["reply"]
    assert all(hit.doc_id != upload["doc_id"] for hit in retriever.search(patient_id, marker, k=5))
    assert len(llm.sent) == before
    spans = tracer.exporter.read()
    assert spans
    assert marker not in repr(spans)
    assert patient_id not in repr(spans)


def test_erasure_removes_upload_file_document_chunks_embeddings_map_and_ingest_link(pgclient, db):
    client, _ = pgclient
    patient_id = "PT-W2-0001"
    upload = upload_marker(client, patient_id, "SYNTHETIC_DELETE_MARKER_28104")
    from services.records.worker import run_once

    assert run_once(db, HashEmbedder())
    db.execute(
        "INSERT INTO document_phi_token_map (doc_id, token_map) VALUES (%s, %s)",
        (upload["doc_id"], Jsonb({"<PERSON_1>": "Synthetic Person"})),
    )
    uri = client.app.state.deps.store.conn.execute(
        "SELECT uri FROM document WHERE doc_id=%s", (upload["doc_id"],)
    ).fetchone()[0]
    source_path = unquote(urlparse(uri).path)
    if source_path.startswith("/") and len(source_path) > 2 and source_path[2] == ":":
        source_path = source_path[1:]
    source_path = Path(source_path)
    assert source_path.is_file()

    response = client.delete(
        f"/records/documents/{upload['doc_id']}",
        headers=token(patient_id),
    )

    assert response.status_code == 204, response.text
    assert not source_path.exists()
    assert db.execute("SELECT count(*) FROM document WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM document_phi_token_map WHERE doc_id=%s", (upload["doc_id"],)
    ).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM ingest_job WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM audit_event WHERE resource_type='document' AND resource_id=%s "
        "AND action='delete' AND patient_id=%s",
        (upload["doc_id"], patient_id),
    ).fetchone()[0] == 1


def test_withdrawn_caregiver_is_refused_on_the_next_w2_request(pgclient, db):
    client, llm = pgclient
    patient_id = "PT-W2-0001"
    caregiver_id = "CG-ERASURE-TEST"
    invalid = client.post(
        "/records/consents",
        headers=token(patient_id),
        json={
            "patient_id": "PT-W2-0002",
            "caregiver_id": caregiver_id,
            "scope": ["records"],
            "purpose": "synthetic regression",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    assert invalid.status_code == 422
    consent = client.post(
        "/records/consents",
        headers=token(patient_id),
        json={
            "caregiver_id": caregiver_id,
            "scope": ["records"],
            "purpose": "synthetic regression",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    assert consent.status_code == 201, consent.text
    allowed = client.post(
        "/w2/ask",
        headers=caregiver_token(caregiver_id, patient_id),
        json={"question": "What is my HbA1c?"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["route"] != "refuse_consent"
    revoked = client.delete(
        f"/records/consents/{caregiver_id}",
        headers=token(patient_id),
    )
    assert revoked.status_code == 204, revoked.text

    before = len(llm.sent)
    response = client.post(
        "/w2/ask",
        headers=caregiver_token(caregiver_id, patient_id),
        json={"question": "What is my HbA1c?"},
    )
    assert response.status_code == 200
    assert response.json()["route"] == "refuse_consent"
    assert "access to these records" in response.json()["reply"].lower()
    assert len(llm.sent) == before
    assert db.execute(
        "SELECT count(*) FROM audit_event WHERE resource_type='consent' AND patient_id=%s "
        "AND action='write' AND detail->>'operation'='withdraw'",
        (patient_id,),
    ).fetchone()[0] == 1


def test_hospital_report_and_signed_w3_note_return_409_with_reasons(pgclient, db):
    client, _ = pgclient
    patient_id = "PT-W2-0001"
    report_id = db.execute(
        "SELECT doc_id FROM document WHERE patient_id=%s ORDER BY doc_id LIMIT 1", (patient_id,)
    ).fetchone()[0]
    report = client.delete(
        f"/records/documents/{report_id}",
        headers=token(patient_id),
    )
    assert report.status_code == 409
    assert "hospital" in report.json()["detail"].lower()

    db.execute(
        "INSERT INTO clinic (clinic_id,name,city,kind,district,opens_at,closes_at,has_pharmacy,synthetic) "
        "VALUES ('ERASE-CLINIC','Synthetic Clinic','Pune','hospital','Synthetic','08:00','20:00',true,true)"
    )
    db.execute("INSERT INTO specialty (specialty_id,name) VALUES ('ERASE-SP','Synthetic')")
    db.execute(
        "INSERT INTO doctor (doctor_id,name,gender,specialty_id,clinic_id,languages,years_practice) "
        "VALUES ('ERASE-DOC','Synthetic Doctor','M','ERASE-SP','ERASE-CLINIC','{en}',1)"
    )
    encounter_id = "ERASE-ENCOUNTER-" + uuid4().hex
    db.execute(
        "INSERT INTO encounter (encounter_id,patient_id,doctor_id,clinic_id,started_at) "
        "VALUES (%s,%s,'ERASE-DOC','ERASE-CLINIC',now())",
        (encounter_id, patient_id),
    )
    note_id = db.execute(
        "INSERT INTO clinical_note (encounter_id,doctor_id,version,soap_json,signed_by,signed_at,content_hash) "
        "VALUES (%s,'ERASE-DOC',1,%s,'ERASE-DOC',now(),'synthetic') RETURNING note_id",
        (encounter_id, Jsonb({"S": "synthetic", "O": "", "A": "", "P": ""})),
    ).fetchone()[0]
    note = client.delete(
        f"/records/documents/{note_id}",
        headers=token(patient_id),
    )
    assert note.status_code == 409
    assert "signed" in note.json()["detail"].lower()


def test_patient_cannot_erase_another_patients_document(pgclient, db):
    client, _ = pgclient
    doc_id = db.execute(
        "SELECT doc_id FROM document WHERE patient_id='PT-W2-0001' ORDER BY doc_id LIMIT 1"
    ).fetchone()[0]
    response = client.delete(
        f"/records/documents/{doc_id}",
        headers=token("PT-W2-0002"),
    )
    assert response.status_code == 403
    assert db.execute("SELECT count(*) FROM document WHERE doc_id=%s", (doc_id,)).fetchone()[0] == 1


def test_erasure_refuses_files_outside_upload_root(pgclient, db, tmp_path):
    client, _ = pgclient
    patient_id = "PT-W2-0001"
    upload = upload_marker(client, patient_id, "SYNTHETIC_PATH_MARKER_18502")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(marker_pdf("SYNTHETIC_OUTSIDE_ROOT"))
    db.execute(
        "UPDATE document SET uri=%s WHERE doc_id=%s",
        (outside.as_uri(), upload["doc_id"]),
    )
    response = client.delete(
        f"/records/documents/{upload['doc_id']}",
        headers=token(patient_id),
    )
    assert response.status_code == 409
    assert "upload root" in response.json()["detail"].lower()
    assert outside.is_file()
    assert db.execute("SELECT count(*) FROM document WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 1


def test_erasure_serializes_with_active_ingest(pgclient, db):
    client, _ = pgclient
    patient_id = "PT-W2-0001"
    upload = upload_marker(client, patient_id, "SYNTHETIC_CONCURRENT_ERASURE_52016")
    entered, release, delete_started = Event(), Event(), Event()

    class BlockingEmbedder(HashEmbedder):
        def embed(self, texts):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release the active ingest")
            return super().embed(texts)

    from services.records.worker import run_once

    worker_conn = worker_connection(db)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            worker = pool.submit(run_once, worker_conn, BlockingEmbedder())
            assert entered.wait(10)

            def erase():
                delete_started.set()
                return client.delete(
                    f"/records/documents/{upload['doc_id']}",
                    headers=token(patient_id),
                )

            deletion = pool.submit(erase)
            assert delete_started.wait(10)
            time.sleep(0.1)
            release.set()
            assert worker.result(timeout=10)
            response = deletion.result(timeout=10)
    finally:
        release.set()
        worker_conn.close()

    assert response.status_code == 204, response.text
    assert db.execute("SELECT count(*) FROM document WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM ingest_job WHERE doc_id=%s", (upload["doc_id"],)).fetchone()[0] == 0

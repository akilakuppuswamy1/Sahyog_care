"""Database-level guarantees. Each test is one promise made to a stakeholder (deck p.7, p.34, p.44).

Needs a Postgres: set DATABASE_URL (docker compose up -d db) or the tests skip.
Each test runs inside a fresh schema so it never touches real data.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from services.agent.w3.store import PostgresW3Store
DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")
DB = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cur():
    schema = f"t_{uuid.uuid4().hex[:8]}"
    conn = psycopg.connect(DSN, autocommit=True)
    c = conn.cursor()
    c.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}, public")
    has_vector = c.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone()
    sql = (DB / "001_schema.sql").read_text()
    if not has_vector:  # plain Postgres in CI: same constraints, embedding stored as real[]
        sql = sql.replace("vector(1024)", "real[]")
    sql = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.M)
    c.execute(sql)
    for migration_path in sorted(DB.glob("00[89]_w3_*.sql")):
        migration = migration_path.read_text()
        migration = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", migration, flags=re.M)
        c.execute(migration)
    c.execute("""
        INSERT INTO clinic VALUES ('PUN-H01','Sahyog Hospital Baner','Pune','hospital','Pune','08:00','20:00',true,true);
        INSERT INTO specialty (specialty_id, name) VALUES ('SP03','Cardiology');
        INSERT INTO doctor VALUES ('DR0001','Dr. Rao','M','SP03','PUN-H01','{en,hi}',20,true);
        INSERT INTO patient (patient_id,name,dob) VALUES ('PT1','Priya','1992-01-01'),('PT2','Asha','1964-05-05');
        INSERT INTO slot (slot_id,doctor_id,clinic_id,starts_at) VALUES ('S1','DR0001','PUN-H01','2026-11-16 09:00+05:30');
    """)
    yield c
    c.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.close()


# -------------------------------------------------------- Meena: no double booking (E2-2)
def test_booking_retry_returns_same_appointment(cur):
    a = cur.execute("SELECT book_slot('PT1','S1','key-1')").fetchone()[0]
    b = cur.execute("SELECT book_slot('PT1','S1','key-1')").fetchone()[0]
    assert a == b
    assert cur.execute("SELECT booked FROM slot WHERE slot_id='S1'").fetchone()[0] == 1


def test_full_slot_cannot_be_booked_again(cur):
    cur.execute("SELECT book_slot('PT1','S1','key-1')")
    with pytest.raises(psycopg.errors.RaiseException, match="full"):
        cur.execute("SELECT book_slot('PT2','S1','key-2')")


def test_capacity_check_constraint_blocks_direct_overfill(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("UPDATE slot SET booked = 2 WHERE slot_id='S1'")


def test_front_desk_override_releases_the_seat(cur):
    appt = cur.execute("SELECT book_slot('PT1','S1','key-1')").fetchone()[0]
    cur.execute("SELECT release_appointment(%s,'overridden')", (appt,))
    assert cur.execute("SELECT booked FROM slot WHERE slot_id='S1'").fetchone()[0] == 0
    assert cur.execute("SELECT book_slot('PT2','S1','key-2')").fetchone()[0] is not None


# -------------------------------------------------------- Priya: consent (E3-2)
def test_consent_valid_only_in_scope_and_before_expiry(cur):
    cur.execute("""INSERT INTO consent (patient_id,grantee_id,scope,purpose,granted_at,expires_at)
                   VALUES ('PT2','PT1','{records}','caring for mother', now() - interval '1 day', now() + interval '7 days')""")
    assert cur.execute("SELECT consent_valid('PT1','PT2','records')").fetchone()[0] is True
    assert cur.execute("SELECT consent_valid('PT1','PT2','appointments')").fetchone()[0] is False
    assert cur.execute("SELECT consent_valid('PT1','PT2','records', now() + interval '8 days')").fetchone()[0] is False
    cur.execute("UPDATE consent SET revoked_at = now() WHERE patient_id='PT2'")
    assert cur.execute("SELECT consent_valid('PT1','PT2','records')").fetchone()[0] is False


def test_consent_rejects_unknown_scope(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("""INSERT INTO consent (patient_id,grantee_id,scope,purpose,expires_at)
                       VALUES ('PT2','PT1','{everything}','x', now() + interval '1 day')""")


# -------------------------------------------------------- Dr. Rao: signed notes (E5-2, D7 tampering)
def _encounter(cur):
    cur.execute("INSERT INTO encounter (encounter_id,patient_id,doctor_id,started_at) VALUES ('E1','PT1','DR0001',now())")


def test_unsigned_note_cannot_be_stored(cur):
    _encounter(cur)
    with pytest.raises(psycopg.errors.NotNullViolation):
        cur.execute("""INSERT INTO clinical_note (encounter_id,doctor_id,version,soap_json,signed_by,content_hash)
                       VALUES ('E1','DR0001',1,'{"S":"","O":"","A":"","P":""}','DR0001','x')""")


def test_signed_note_is_immutable_and_hashed(cur):
    _encounter(cur)
    nid = cur.execute("""INSERT INTO clinical_note (encounter_id,doctor_id,version,soap_json,signed_by,signed_at,content_hash)
                         VALUES ('E1','DR0001',1,'{"S":"a","O":"b","A":"c","P":"d"}','DR0001',now(),'placeholder')
                         RETURNING note_id""").fetchone()[0]
    assert len(cur.execute("SELECT content_hash FROM clinical_note").fetchone()[0]) == 64
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        cur.execute("UPDATE clinical_note SET soap_json = '{\"S\":\"x\",\"O\":\"\",\"A\":\"\",\"P\":\"\"}' WHERE note_id=%s", (nid,))
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        cur.execute("DELETE FROM clinical_note WHERE note_id=%s", (nid,))


def test_only_treating_doctor_can_sign(cur):
    _encounter(cur)
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("""INSERT INTO clinical_note (encounter_id,doctor_id,version,soap_json,signed_by,signed_at,content_hash)
                       VALUES ('E1','DR0001',1,'{"S":"","O":"","A":"","P":""}','DR9999',now(),'x')""")


def test_w3_draft_edit_and_signed_note_are_persisted_separately(cur):
    _encounter(cur)
    draft_id = uuid.uuid4()
    cur.execute(
        """INSERT INTO note_draft
               (draft_id,encounter_id,doctor_id,version,soap_json,flags,status,review_started_at)
           VALUES (%s,'E1','DR0001',1,'{"S":"","O":"","A":"","P":""}','[]','draft',now())""",
        (draft_id,),
    )
    cur.execute(
        """INSERT INTO note_edit (draft_id,field,"before","after",edited_by)
           VALUES (%s,'P','"old"','"new"','DR0001')""",
        (draft_id,),
    )
    note_id = cur.execute(
        """INSERT INTO clinical_note
               (encounter_id,doctor_id,version,soap_json,signed_by,signed_at,content_hash)
           VALUES ('E1','DR0001',1,'{"S":"","O":"","A":"","P":"new"}','DR0001',now(),'pending')
           RETURNING note_id"""
    ).fetchone()[0]
    cur.execute("UPDATE note_draft SET status='signed',signed_note_id=%s WHERE draft_id=%s",
                (note_id, draft_id))

    assert cur.execute("SELECT status,signed_note_id FROM note_draft WHERE draft_id=%s",
                       (draft_id,)).fetchone() == ("signed", note_id)
    assert cur.execute("SELECT field,\"before\",\"after\" FROM note_edit WHERE draft_id=%s",
                       (draft_id,)).fetchone() == ("P", "old", "new")
    assert len(cur.execute("SELECT content_hash FROM clinical_note WHERE note_id=%s",
                           (note_id,)).fetchone()[0]) == 64


def test_database_rejects_non_treating_doctor_note(cur):
    _encounter(cur)
    cur.execute("INSERT INTO doctor VALUES ('DR0002','Dr. Iyer','F','SP03','PUN-H01','{en}',12,true)")
    with pytest.raises(psycopg.errors.CheckViolation, match="treating doctor"):
        cur.execute(
            """INSERT INTO clinical_note
                   (encounter_id,doctor_id,version,soap_json,signed_by,signed_at,content_hash)
               VALUES ('E1','DR0002',1,'{"S":"","O":"","A":"","P":""}','DR0002',now(),'pending')"""
        )


def test_postgres_w3_signing_hashes_versions_audits_and_notifies(cur):
    _encounter(cur)
    store = PostgresW3Store(cur.connection)
    first_id = str(uuid.uuid4())
    first_soap = {
        "S": "", "O": "", "A": "", "P": "Initial plan.",
        "medicines": [{"name": "Atorva", "molecule": "atorvastatin", "dose": "10 mg",
                       "stock_status": "out_of_stock", "alternatives": [
                           {"medicine_id": "MED2", "name": "Atorlip", "suggestion_only": True},
                       ]}],
    }
    first = {
        "draft_id": first_id, "encounter_id": "E1", "doctor_id": "DR0001", "version": 1,
        "soap_json": first_soap, "initial_soap_json": first_soap,
        "medicines": [{"medicine_id": "MED1", "name": "Atorva", "molecule": "atorvastatin", "dose": "10 mg"}],
        "flags": [{"kind": "stock", "message": "out of stock"}], "edit_history": [],
    }
    store.save_draft(first)
    signed_first = store.sign_draft(first, "DR0001", "w3-trace")
    row = cur.execute(
        "SELECT version,content_hash,supersedes FROM clinical_note WHERE note_id=%s",
        (signed_first["note_id"],),
    ).fetchone()
    assert row[0] == 1 and len(row[1]) == 64 and row[2] is None
    prescription = cur.execute(
        "SELECT medicine_id,medicine_text,substitute_ok FROM prescription WHERE note_id=%s",
        (signed_first["note_id"],),
    ).fetchone()
    assert prescription == ("MED1", "Atorva", False)
    audit_detail = cur.execute(
        "SELECT detail FROM audit_event WHERE resource_id=%s AND action='sign'",
        (signed_first["note_id"],),
    ).fetchone()[0]
    assert audit_detail["stock_flags"] == [{"kind": "stock", "message": "out of stock"}]

    second_id = str(uuid.uuid4())
    second = {
        **first, "draft_id": second_id, "version": 2,
        "soap_json": {"S": "", "O": "", "A": "", "P": "Revised plan.", "medicines": []},
    }
    store.save_draft(second)
    store.record_edit(second_id, "P", "Initial plan.", "Revised plan.", "DR0001")
    second["edit_history"] = [{"field": "P", "before": "Initial plan.", "after": "Revised plan."}]
    signed_second = store.sign_draft(second, "DR0001", "w3-trace")
    assert signed_second["version"] == 2
    assert signed_second["supersedes"] == signed_first["note_id"]
    assert signed_second["edit_distance_by_field"]["P"] > 0
    assert cur.execute("SELECT count(*) FROM clinical_note WHERE encounter_id='E1'").fetchone()[0] == 2
    assert store.notify_signed(signed_second, "PT1", "w3-trace")["type"] == "clinical_note_signed"
    assert cur.execute(
        "SELECT count(*) FROM audit_event WHERE trace_id='w3-trace' AND action='sign'"
    ).fetchone()[0] == 2


# -------------------------------------------------------- Priya: no cross-patient chunks (E4-4)
def test_chunk_patient_must_match_its_document(cur):
    cur.execute("INSERT INTO document (doc_id,patient_id,type,uri) VALUES ('D1','PT1','lab','s3://x')")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        cur.execute("INSERT INTO doc_chunk (chunk_id,doc_id,patient_id,page,text) VALUES ('C1','D1','PT2',1,'hb 9.1')")


# -------------------------------------------------------- Kavita: audit (E6-1)
def test_audit_is_append_only_and_answers_who_saw(cur):
    cur.execute("""INSERT INTO audit_event (actor_id,actor_role,action,resource_type,resource_id,patient_id,purpose)
                   VALUES ('DR0001','doctor','read','document','D1','PT1','treatment')""")
    rows = cur.execute("SELECT actor_id, purpose FROM who_saw_record WHERE patient_id='PT1'").fetchall()
    assert rows == [("DR0001", "treatment")]
    with pytest.raises(psycopg.errors.RaiseException):
        cur.execute("DELETE FROM audit_event")

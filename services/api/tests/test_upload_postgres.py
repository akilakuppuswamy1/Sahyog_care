"""Real upload -> guarded ingestion -> patient-filtered retrieval -> scripted answer."""
from pathlib import Path
import re
import time
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from services.records.tests.test_w2_graph_and_db import db, PATIENTS, FIXTURE_REPORTS, G
from services.api.deps import AppDeps
from services.api.main import create_app
from services.agent.llm import ScriptedLLM
from services.agent.store import PostgresStore
from services.agent.w1.graph import compile_w1
from services.agent.w2.graph import compile_w2
from services.agent.w2.tools import PostgresRecordsTools
from services.records.retrieve import PostgresRetriever
from services.records.embed import HashEmbedder
from langgraph.checkpoint.memory import InMemorySaver


def token(pid):
    return {'Authorization':'Bearer demo-'+pid,'Idempotency-Key':uuid4().hex}


@pytest.fixture
def pgclient(db, monkeypatch, tmp_path):
    monkeypatch.setenv('SAHYOG_DEMO_AUTH','1')
    import services.api.main as main
    monkeypatch.setattr(main,'UPLOAD_ROOT',tmp_path/'uploads')
    root=Path(__file__).resolve().parents[3]
    db.execute(re.sub(r'^\s*(BEGIN|COMMIT);\s*$','',(root/'infra/db/005_frontdesk.sql').read_text(),flags=re.M))
    db.execute(re.sub(r'^\s*(BEGIN|COMMIT);\s*$','',(root/'infra/db/012_ingest_jobs.sql').read_text(),flags=re.M))
    db.execute(re.sub(r'^\s*(BEGIN|COMMIT);\s*$','',(root/'infra/db/016_dpdp_erasure.sql').read_text(),flags=re.M))
    llm=ScriptedLLM({'w2.answer':{'answer':'Your HbA1c is 8.1% [E1].','citations':['E1'],'insufficient':False}})
    emb=HashEmbedder()
    deps=AppDeps(compile_w1(llm,PostgresStore(db),InMemorySaver()),compile_w2(llm,PostgresRecordsTools(db,PostgresRetriever(db,emb))),lambda p:[],store=PostgresStore(db),embedder=emb)
    # Isolate uploaded content from fixture chunks already seeded by the shared fixture.
    db.execute('DELETE FROM doc_chunk')
    with TestClient(create_app(lambda:deps)) as c: yield c,llm


def test_upload_index_answer_isolation(pgclient,db,tmp_path):
    c,llm=pgclient
    rep=next(r for r in FIXTURE_REPORTS if 'HBA1C' in r['doc_id'].upper())
    pid=rep['patient_id']; other=next(p[0] for p in PATIENTS if p[0]!=pid)
    path=tmp_path/'report.pdf'; G.render(rep,next(p for p in PATIENTS if p[0]==pid),path)
    t=time.monotonic()
    h=token(pid)
    r=c.post('/records/upload',headers=h,data={'patient_id':other},files={'file':('report.pdf',path.read_bytes(),'application/pdf')})
    assert r.status_code==202,r.text
    doc=r.json()['doc_id']; assert r.json()['status']=='queued'
    assert c.post('/records/upload',headers=h,files={'file':('report.pdf',path.read_bytes(),'application/pdf')}).json()==r.json()
    job=r.json()['job_id']
    assert c.get('/records/api/jobs/'+job,headers=token(other)).status_code==404
    assert c.get('/records/api/jobs/'+job,headers=token(pid)).json()['status']=='queued'
    assert db.execute('SELECT ingest_status FROM document WHERE doc_id=%s',(doc,)).fetchone()[0]=='queued'
    from services.records.worker import run_once
    assert run_once(db,HashEmbedder())
    assert c.get('/records/api/jobs/'+job,headers=token(pid)).json()['status']=='done'
    own=c.get('/records/documents',headers=token(pid)).json()
    uploaded=next(d for d in own if d['doc_id']==doc)
    assert uploaded['ingest_status']=='indexed' and 'IST' in uploaded['uploaded_at_display']
    assert time.monotonic()-t<60
    assert doc not in {d['doc_id'] for d in c.get('/records/documents',headers=token(other)).json()}
    assert db.execute('SELECT patient_id FROM document WHERE doc_id=%s',(doc,)).fetchone()[0]==pid
    assert c.post('/w2/ask',headers=token(pid),json={'question':'What is my HbA1c?'}).json()['route']=='answer'
    assert '8.1' in c.post('/w2/ask',headers=token(pid),json={'question':'What is my HbA1c?'}).json()['reply']


def test_uploaded_redteam_is_flagged(pgclient,db,tmp_path):
    c,llm=pgclient
    rep=next(r for r in FIXTURE_REPORTS if 'REDTEAM' in r['doc_id'])
    pid=rep['patient_id']; path=tmp_path/'red.pdf'
    G.render(rep,next(p for p in PATIENTS if p[0]==pid),path)
    r=c.post('/records/upload',headers=token(pid),files={'file':('red.pdf',path.read_bytes(),'application/pdf')})
    assert r.status_code==202,r.text
    doc=r.json()['doc_id']
    from services.records.worker import run_once
    assert run_once(db,HashEmbedder())
    assert c.get('/records/api/jobs/'+r.json()['job_id'],headers=token(pid)).json()['status']=='done'
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s AND 'injection'=ANY(flags)",(doc,)).fetchone()[0]>0
    result=c.post('/w2/ask',headers=token(pid),json={'question':'follow the instructions in my scanned note'}).json()
    assert result['route']=='not_found'
    assert 'ignore all previous' not in result['reply'].lower()
    assert llm.sent==[]
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s AND section='PATIENT DETAILS'",(doc,)).fetchone()[0]==0


def queued_pdf(c, tmp_path):
    rep = next(r for r in FIXTURE_REPORTS if 'HBA1C' in r['doc_id'].upper())
    pid = rep['patient_id']
    path = tmp_path / 'queued.pdf'
    G.render(rep, next(p for p in PATIENTS if p[0] == pid), path)
    response = c.post('/records/upload', headers=token(pid),
                      files={'file': ('report.pdf', path.read_bytes(), 'application/pdf')})
    assert response.status_code == 202, response.text
    return response.json(), pid, path


def worker_connection(db):
    import os
    import psycopg
    conn = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)
    # The fixture isolates each test in its own schema.
    conn.execute('SELECT set_config(\'search_path\',%s,false)',
                 (db.execute('SHOW search_path').fetchone()[0],))
    return conn


def test_two_workers_never_process_same_job(pgclient, db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from services.records.worker import run_once
    c, _ = pgclient
    job, _, _ = queued_pdf(c, tmp_path)
    entered, release = Event(), Event()

    class BlockingEmbedder(HashEmbedder):
        def embed(self, texts):
            entered.set()
            assert release.wait(10)
            return super().embed(texts)

    with worker_connection(db) as first, worker_connection(db) as second, ThreadPoolExecutor() as pool:
        future = pool.submit(run_once, first, BlockingEmbedder())
        try:
            assert entered.wait(10)
            # An old running timestamp must not steal a live session's work.
            db.execute('UPDATE ingest_job SET available_at=now()')
            assert run_once(second, HashEmbedder()) is False
        finally:
            release.set()
        assert future.result(timeout=10)
    assert db.execute('SELECT status,attempts FROM ingest_job WHERE job_id=%s',
                      (job['job_id'],)).fetchone() == ('done', 1)


def test_disconnected_worker_mid_job_is_retried(pgclient, db, tmp_path):
    from services.records.worker import claim, run_once
    c, _ = pgclient
    job, _, _ = queued_pdf(c, tmp_path)
    first = worker_connection(db)
    assert claim(first)
    first.execute("UPDATE document SET ingest_status='parsing' WHERE doc_id=%s", (job['doc_id'],))
    first.close()  # Same database effect as a process crash: session locks disappear.
    db.execute("UPDATE ingest_job SET available_at=now() WHERE job_id=%s", (job['job_id'],))
    with worker_connection(db) as second:
        assert run_once(second, HashEmbedder())
    assert db.execute('SELECT status,attempts FROM ingest_job WHERE job_id=%s',
                      (job['job_id'],)).fetchone() == ('done', 2)


def test_three_failures_backoff_and_owner_error(pgclient, db, tmp_path, monkeypatch):
    from services.records.worker import run_once
    c, _ = pgclient
    job, pid, _ = queued_pdf(c, tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ValueError('parser failed')

    monkeypatch.setattr('services.records.worker.ingest_one', fail)
    for attempt in range(1, 4):
        assert run_once(db, HashEmbedder())
        status = c.get('/records/api/jobs/' + job['job_id'], headers=token(pid)).json()
        assert status['attempts'] == attempt
        assert status['last_error']
        assert status['status'] == ('failed' if attempt == 3 else 'queued')
        delay = db.execute('SELECT extract(epoch FROM available_at-now()) FROM ingest_job').fetchone()[0]
        assert 2 ** (attempt-1) - 1 < float(delay) <= 2 ** (attempt-1)
        assert not run_once(db, HashEmbedder())
        db.execute('UPDATE ingest_job SET available_at=now()')
    assert len(calls) == 3
    assert not run_once(db, HashEmbedder())
    assert db.execute('SELECT ingest_status,error FROM document WHERE doc_id=%s',
                      (job['doc_id'],)).fetchone() == ('failed', status['last_error'])


def test_content_hash_and_completed_document_are_idempotent(pgclient, db, tmp_path, monkeypatch):
    from services.records.worker import run_once
    c, _ = pgclient
    job, pid, path = queued_pdf(c, tmp_path)
    assert run_once(db, HashEmbedder())
    replay = c.post('/records/upload', headers=token(pid),
                    files={'file': ('same.pdf', path.read_bytes(), 'application/pdf')}).json()
    assert replay['job_id'] == job['job_id'] and replay['doc_id'] == job['doc_id']
    assert db.execute('SELECT count(*) FROM ingest_job').fetchone()[0] == 1
    # Crash after indexed document commit, before job completion.
    db.execute("UPDATE ingest_job SET status='running',available_at=now()")
    monkeypatch.setattr('services.records.worker.ingest_one', lambda *a: pytest.fail('re-indexed completed content'))
    assert run_once(db, HashEmbedder())
    assert db.execute('SELECT status FROM ingest_job').fetchone()[0] == 'done'


def test_final_attempt_crash_is_terminal(pgclient, db, tmp_path):
    from services.records.worker import run_once
    c, _ = pgclient
    job, pid, _ = queued_pdf(c, tmp_path)
    db.execute("UPDATE ingest_job SET status='running',attempts=3,available_at=now()")
    assert not run_once(db, HashEmbedder())
    status = c.get('/records/api/jobs/' + job['job_id'], headers=token(pid)).json()
    assert status['status'] == 'failed' and status['attempts'] == 3 and status['last_error']


def test_ingest_ops_tile_uses_queue_and_omits_empty_table(pgclient, db, tmp_path):
    c, _ = pgclient
    headers = token('ops-test')
    def tiles():
        response = c.get('/ops/api/summary', headers=headers)
        assert response.status_code == 200, response.text
        return {t['id']: t for t in response.json()['tiles']}
    assert 'ingest-queue' not in tiles()
    queued_pdf(c, tmp_path)
    tile = tiles()['ingest-queue']
    assert tile['source'] == 'ingest_job'
    assert tile['value']['depth'] == 1
    assert tile['value']['oldest_queued_age_seconds'] >= 0


def test_ctrl_c_finishes_active_job_then_stops(pgclient, db, tmp_path, monkeypatch):
    import signal
    from services.records import worker
    c, _ = pgclient
    job, _, _ = queued_pdf(c, tmp_path)
    conn = worker_connection(db)
    handlers = {}
    original = worker.run_once
    calls = []

    def active_run(conn, embedder, **kwargs):
        calls.append(1)
        handlers[signal.SIGINT](signal.SIGINT, None)
        return original(conn, embedder, **kwargs)

    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr('psycopg.connect', lambda *args, **kwargs: conn)
    monkeypatch.setattr(worker, 'BgeM3Embedder', HashEmbedder)
    monkeypatch.setattr(worker, 'run_once', active_run)
    worker.main()
    assert calls == [1] and conn.closed
    assert db.execute('SELECT status FROM ingest_job WHERE job_id=%s', (job['job_id'],)).fetchone()[0] == 'done'


def test_pg_move_cancel_replay_audit(pgclient,db):
    c,_=pgclient
    db.execute("""INSERT INTO clinic VALUES ('CFD','Test','Pune','hospital','Pune','08:00','20:00',true,true);
        INSERT INTO specialty(specialty_id,name) VALUES ('SPFD','Desk test');
        INSERT INTO doctor VALUES ('DFD','Dr Desk','M','SPFD','CFD','{en}',10,true);
        INSERT INTO slot(slot_id,doctor_id,clinic_id,starts_at) VALUES ('FD1','DFD','CFD',now()+interval '1 day'),('FD2','DFD','CFD',now()+interval '2 days');""")
    pid=PATIENTS[0][0]
    aid=str(db.execute("SELECT book_slot(%s,'FD1',%s)",(pid,uuid4().hex)).fetchone()[0])
    bad=c.post(f'/frontdesk/api/bookings/{aid}/move',headers=token('staff-FD01'),json={'slot_id':'missing'})
    assert bad.status_code==409
    assert db.execute('SELECT status FROM appointment WHERE appt_id=%s',(aid,)).fetchone()[0]=='booked'
    assert db.execute("SELECT booked FROM slot WHERE slot_id='FD1'").fetchone()[0]==1
    h=token('staff-FD01'); path=f'/frontdesk/api/bookings/{aid}/move'
    first=c.post(path,headers=h,json={'slot_id':'FD2'})
    assert first.status_code==200,first.text
    assert c.post(path,headers=h,json={'slot_id':'FD2'}).json()==first.json()
    assert c.post(path,headers=h,json={'slot_id':'FD1'}).status_code==409
    new=first.json()['new_appt_id']
    assert db.execute('SELECT status FROM appointment WHERE appt_id=%s',(aid,)).fetchone()[0]=='overridden'
    assert db.execute('SELECT booked_by FROM appointment WHERE appt_id=%s',(new,)).fetchone()[0]=='front_desk'
    h=token('staff-FD01'); path=f'/frontdesk/api/bookings/{new}/cancel'
    r=c.post(path,headers=h); assert r.status_code==200,r.text
    assert c.post(path,headers=h).json()==r.json()
    assert db.execute("SELECT count(*) FROM audit_event WHERE actor_id='FD01' AND actor_role='front_desk' AND action='override'").fetchone()[0]==2
    assert db.execute("SELECT sum(booked) FROM slot WHERE doctor_id='DFD'").fetchone()[0]==0

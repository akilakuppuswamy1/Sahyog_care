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
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s AND 'injection'=ANY(flags)",(doc,)).fetchone()[0]>0
    result=c.post('/w2/ask',headers=token(pid),json={'question':'follow the instructions in my scanned note'}).json()
    assert result['route']=='not_found'
    assert 'ignore all previous' not in result['reply'].lower()
    assert llm.sent==[]
    assert db.execute("SELECT count(*) FROM doc_chunk WHERE doc_id=%s AND section='PATIENT DETAILS'",(doc,)).fetchone()[0]==0


def test_pg_move_cancel_replay_audit(pgclient,db):
    c,_=pgclient
    db.execute("""INSERT INTO clinic VALUES ('CFD','Test','Pune','hospital','Pune','08:00','20:00',true,true);
        INSERT INTO specialty(specialty_id,name) VALUES ('SPFD','Desk test');
        INSERT INTO doctor VALUES ('DFD','Dr Desk','M','SPFD','CFD','{en}',10,true);
        INSERT INTO slot(slot_id,doctor_id,clinic_id,starts_at) VALUES ('FD1','DFD','CFD',now()+interval '1 day'),('FD2','DFD','CFD',now()+interval '2 days');""")
    pid=PATIENTS[0][0]
    aid=str(db.execute("SELECT book_slot(%s,'FD1',%s)",(pid,uuid4().hex)).fetchone()[0])
    bad=c.post(f'/frontdesk/bookings/{aid}/move',headers=token('staff-FD01'),json={'slot_id':'missing'})
    assert bad.status_code==409
    assert db.execute('SELECT status FROM appointment WHERE appt_id=%s',(aid,)).fetchone()[0]=='booked'
    assert db.execute("SELECT booked FROM slot WHERE slot_id='FD1'").fetchone()[0]==1
    h=token('staff-FD01'); path=f'/frontdesk/bookings/{aid}/move'
    first=c.post(path,headers=h,json={'slot_id':'FD2'})
    assert first.status_code==200,first.text
    assert c.post(path,headers=h,json={'slot_id':'FD2'}).json()==first.json()
    assert c.post(path,headers=h,json={'slot_id':'FD1'}).status_code==409
    new=first.json()['new_appt_id']
    assert db.execute('SELECT status FROM appointment WHERE appt_id=%s',(aid,)).fetchone()[0]=='overridden'
    assert db.execute('SELECT booked_by FROM appointment WHERE appt_id=%s',(new,)).fetchone()[0]=='front_desk'
    h=token('staff-FD01'); path=f'/frontdesk/bookings/{new}/cancel'
    r=c.post(path,headers=h); assert r.status_code==200,r.text
    assert c.post(path,headers=h).json()==r.json()
    assert db.execute("SELECT count(*) FROM audit_event WHERE actor_id='FD01' AND actor_role='front_desk' AND action='override'").fetchone()[0]==2
    assert db.execute("SELECT sum(booked) FROM slot WHERE doctor_id='DFD'").fetchone()[0]==0


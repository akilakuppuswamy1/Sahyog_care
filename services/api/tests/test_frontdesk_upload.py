from datetime import datetime, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from services.api.deps import build_test_deps
from services.api.main import create_app
from services.common.timefmt import ist


def token(who='PT-A', key=None):
    return {'Authorization':'Bearer demo-'+who,'Idempotency-Key':key or uuid4().hex}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('SAHYOG_DEMO_AUTH','1')
    with TestClient(create_app(build_test_deps)) as c: yield c


@pytest.mark.parametrize('ts',['2026-11-16T03:30:00+00:00',datetime(2026,11,16,3,30,tzinfo=timezone.utc),'2026-11-16T09:00:00+05:30'])
def test_ist(ts):
    assert ist(ts)=='Mon 16 Nov, 9:00 AM IST'


@pytest.mark.parametrize('text',['What is my HbA1c?','mera report dikhao','what does my x-ray say'])
def test_redirect(client,text):
    out=client.post('/w1/triage',headers=token(),json={'text':text}).json()
    assert out['route']=='redirect_records' and not out['booking_id'] and not out['interrupt']
    assert out['reply']=='That sounds like a question about your reports. Ask it under My records.'
    assert client.app.state.deps.w1_llm.sent==[]
    assert not client.app.state.deps.store.booked


def test_red_flag_queue_and_handling(client):
    r=client.post('/w1/triage',headers=token(),json={'text':'what is my HbA1c, also crushing chest pain right now'}).json()
    assert r['route']=='emergency_desk' and client.app.state.deps.w1_llm.sent==[]
    q=client.get('/frontdesk/api/escalations',headers=token('staff-FD1')).json()
    assert len(q)==1 and q[0]['patient_id']=='PT-A'
    path=f"/frontdesk/api/escalations/{q[0]['id']}/handled"
    assert client.post(path,headers=token('staff-FD1')).status_code==200
    assert client.post(path,headers=token('staff-FD2')).status_code==200
    assert client.get('/frontdesk/api/escalations',headers=token('staff-FD1')).json()==[]
    assert client.get('/frontdesk/api/escalations?status=handled',headers=token('staff-FD1')).json()[0]['status']=='handled'


@pytest.mark.parametrize('method,path,body',[
 ('get','/frontdesk/api/escalations',None),('get','/frontdesk/api/bookings',None),
 ('post','/frontdesk/api/escalations/1/handled',None),('post','/frontdesk/api/bookings/missing/cancel',None),
 ('post','/frontdesk/api/bookings/missing/move',{'slot_id':'S1'})])
def test_patient_cannot_staff(client,method,path,body):
    assert getattr(client,method)(path,headers=token(),**({'json':body} if body else {})).status_code==403


@pytest.mark.parametrize('method,path,body',[
 ('get','/records/access-log',None),('get','/records/documents',None),
 ('post','/w1/triage',{'text':'pain'}),('post','/w1/missing/resume',{'value':'x'}),
 ('post','/w2/ask',{'question':'HbA1c?'})])
def test_staff_cannot_patient(client,method,path,body):
    assert getattr(client,method)(path,headers=token('staff-FD1'),**({'json':body} if body else {})).status_code==403


def test_staff_cannot_upload(client):
    assert client.post('/records/upload',headers=token('staff-FD1'),files={'file':('x.pdf',b'%PDF-test','application/pdf')}).status_code==403


@pytest.mark.parametrize('move',[False,True])
def test_override_replay_audit(client,move):
    store=client.app.state.deps.store
    aid=store.book('PT-A',store.doctors[0]['slot_id'],uuid4().hex)
    slot=store.doctors[1]['slot_id']
    path=f"/frontdesk/api/bookings/{aid}/"+('move' if move else 'cancel')
    h=token('staff-FD1')
    response=client.post(path,headers=h,**({'json':{'slot_id':slot}} if move else {}))
    assert response.status_code==200,response.text
    out=response.json()
    assert client.post(path,headers=h,**({'json':{'slot_id':slot}} if move else {})).json()==out
    assert store.appointments[aid]['status']=='overridden'
    assert store.doctors[0]['slot_id'] not in store.booked
    if move: assert store.appointments[out['new_appt_id']]['booked_by']=='front_desk'
    events=[e for e in store.events if e.get('actor_role')=='front_desk']
    assert all(e['actor_id']=='FD1' for e in events)
    assert sum(e['action']=='override' for e in events)==1
    assert client.get('/frontdesk/api/bookings',headers=token('staff-FD1')).status_code==200


@pytest.mark.parametrize('data,mime,status',[
    (b'hello','application/pdf',415),
    (b'%PDF-'+b'x'*(10*1024*1024),'application/pdf',413),
    (b'%PDF-x','text/plain',415),
],ids=['invalid-signature','oversize','wrong-content-type'])
def test_upload_rejection(client,data,mime,status):
    assert client.post('/records/upload',headers=token(),files={'file':('x.pdf',data,mime)}).status_code==status


def test_access_log_adds_ist_preserves_iso(client):
    deps=client.app.state.deps
    deps.audit_query=lambda user:[dict(at="2026-11-16T03:30:00+00:00",patient_id=user)]
    r=client.get('/records/access-log',headers=token()).json()[0]
    assert r['at']=='2026-11-16T03:30:00+00:00'
    assert r['at_display']=='Mon 16 Nov, 9:00 AM IST'


def test_confirmation_ist(client):
    out=client.post('/w1/triage',headers=token(),json={'text':'Knee pain for months'}).json()
    slot=out['interrupt']['options'][0]['slot_id']
    r=client.post('/w1/'+out['thread_id']+'/resume',headers=token(),json={'value':slot}).json()
    assert 'IST' in r['reply'] and 'T10:00' not in r['reply']

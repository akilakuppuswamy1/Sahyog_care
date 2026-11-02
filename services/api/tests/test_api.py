import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from services.api.deps import build_test_deps
from services.api.main import create_app


def token(patient="PT-A"):
    return {"Authorization": f"Bearer demo-{patient}", "Idempotency-Key": str(uuid4())}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    with TestClient(create_app(build_test_deps)) as client:
        yield client


def test_auth(client, monkeypatch):
    assert client.get('/health').json() == {"status": "ok"}
    assert client.get('/health').status_code == 200
    assert client.post('/w2/ask', json={"question": "Hb?"}).status_code == 401
    monkeypatch.delenv("SAHYOG_DEMO_AUTH")
    assert client.post('/w2/ask', headers=token(), json={"question": "Hb?"}).status_code == 401


def test_red_flag(client):
    response = client.post('/w1/triage', headers=token(),
                           json={"text": "crushing chest pain and sweating"})
    assert response.status_code == 200
    out = response.json()
    assert out['status'] == 'done' and out['red_flags'] and '112' in out['reply']
    assert out['interrupt'] is None and not out['booking_id']
    assert client.app.state.deps.w1_llm.sent == []
    assert client.post(f"/w1/{out['thread_id']}/resume", headers=token(),
                       json={"value": "anything"}).status_code == 409


def test_booking_and_ownership(client):
    out = client.post('/w1/triage', headers=token(), json={
        "text": "Dull lower back pain for three weeks, worse after sitting.", "age": 41}).json()
    assert out['status'] == 'needs_input'
    path = f"/w1/{out['thread_id']}/resume"
    assert client.post(path, headers=token('PT-B'), json={"value": "anything"}).status_code == 404
    if out['interrupt']['type'] == 'clarify':
        out = client.post(path, headers=token(), json={"value": "Lower back, for three weeks"}).json()
    slot = out['interrupt']['options'][0]['slot_id']
    out = client.post(path, headers=token(), json={"value": slot}).json()
    assert out['status'] == 'done' and out['booking_id']
    assert client.post('/w1/missing/resume', headers=token(), json={"value": slot}).status_code == 404


@pytest.mark.parametrize('path,body', [('/w1/triage', {"text": "pain"}),
                                     ('/w2/ask', {"question": "Hb?"}),
                                     ('/w1/missing/resume', {"value": "answer"})])
def test_body_cannot_supply_patient(client, path, body):
    assert client.post(path, headers=token(), json={**body, "patient_id": "PT-B"}).status_code == 422


def test_consent_and_audit_scope(client):
    out = client.post('/w2/ask', headers=token(), json={"question": "Hb?", "subject_id": "PT-B"}).json()
    assert out['route'] == 'refuse_consent'
    assert set(out) == {'route', 'reply', 'verified', 'trace_id'}
    client.post('/w2/ask', headers=token('PT-B'), json={"question": "Hb?", "subject_id": "PT-A"})
    rows = client.get('/records/access-log', headers=token()).json()
    assert rows and all(row['patient_id'] == 'PT-A' for row in rows)
    assert client.get('/records/access-log', headers=token('PT-C')).json() == []


def test_static_page(client):
    response = client.get('/')
    assert response.status_code == 200 and 'Signed in as' in response.text


def assert_concurrent_triage(client, llm, headers):
    """Block a model call on a worker thread while replay and GET run."""
    import time
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor

    entered, release = Event(), Event()
    original = llm.complete_json
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        time.sleep(0.05)
        return original(*args, **kwargs)
    llm.complete_json = slow
    body = {"text": "Knee pain climbing stairs for months", "age": 61}
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(client.post, '/w1/triage', headers=headers, json=body)
        try:
            assert entered.wait(10)
            replay = workers.submit(client.post, '/w1/triage', headers=headers, json=body).result(timeout=5)
            assert replay.status_code == 202, replay.text
            running = replay.json()
            thread = running['thread_id']
            assert thread and running['status'] == 'running'
            assert client.get('/w1/'+thread, headers=headers).json() == running
            assert client.get('/w1/'+thread, headers=token('PT-OTHER')).status_code == 404
        finally:
            release.set()
        response = first.result(timeout=10)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out['thread_id'] == thread and out['status'] == 'needs_input'
    replay = client.post('/w1/triage', headers=headers, json=body)
    assert replay.json() == out
    assert client.get('/w1/'+thread, headers=headers).json() == out
    slot = out['interrupt']['options'][0]['slot_id']
    resume_headers = {**headers, 'Idempotency-Key': uuid4().hex}
    path = '/w1/'+thread+'/resume'
    booked = client.post(path, headers=resume_headers, json={'value': slot})
    assert booked.status_code == 200, booked.text
    assert booked.json()['booking_id'] and booked.json()['status'] == 'done'
    assert client.post(path, headers=resume_headers, json={'value': slot}).json() == booked.json()
    assert client.post('/w1/triage', headers=headers, json=body).json() == booked.json()
    return thread


def test_slow_concurrent_triage_replay_and_booking(client):
    deps = client.app.state.deps
    thread = assert_concurrent_triage(client, deps.w1_llm, token())
    assert len(deps.w1_requests.rows) == 1
    assert next(iter(deps.w1_requests.rows.values()))['thread_id'] == thread

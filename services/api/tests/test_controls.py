import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.deps import build_test_deps
from services.api.main import create_app
from services.api.ratelimit import RateLimiter
from services.agent.llm import ModelUnavailable


def headers(patient="PT-A", key=None):
    return {"Authorization": f"Bearer demo-{patient}", "Idempotency-Key": key or str(uuid4())}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    with TestClient(create_app(build_test_deps)) as client:
        yield client


@pytest.mark.parametrize("path,body", [("/w1/triage", {"text": "pain"}),
                                      ("/w1/missing/resume", {"value": "slot"})])
@pytest.mark.parametrize("key", [None, "short", "x" * 129])
def test_required_key(client, path, body, key):
    h = {"Authorization": "Bearer demo-PT-A"}
    if key is not None:
        h["Idempotency-Key"] = key
    response = client.post(path, headers=h, json=body)
    assert response.status_code == 400
    assert response.headers["X-Trace-Id"]


def start(client):
    return client.post("/w1/triage", headers=headers(),
                       json={"text": "Lower back ache for three weeks", "age": 41}).json()


def test_booking_replay(client):
    out = start(client)
    path = f"/w1/{out['thread_id']}/resume"
    h = headers()
    body = {"value": out["interrupt"]["options"][0]["slot_id"]}
    first = client.post(path, headers=h, json=body)
    second = client.post(path, headers=h, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["booking_id"] == second.json()["booking_id"]
    assert first.json()["booking_id"]
    store = client.app.state.deps.store
    assert len(store.booked) == 1
    assert store.keys["PT-A", h["Idempotency-Key"]] == first.json()["booking_id"]


def test_read_limit(client):
    for _ in range(30):
        assert client.get("/records/access-log", headers=headers()).status_code == 200
    response = client.get("/records/access-log", headers=headers())
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
    assert client.app.state.deps.store.events[-1]["action"] == "rate_limited"


def test_booking_limit(client):
    # Each invalid choice still resumes pick_slot and must consume the write bucket.
    with TestClient(client.app, raise_server_exceptions=False) as attempts:
        out = start(attempts)
        path = f"/w1/{out['thread_id']}/resume"
        for _ in range(20):
            assert attempts.post(path, headers=headers(), json={"value": "invalid"}).status_code == 400
        response = attempts.post(path, headers=headers(), json={"value": "invalid"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0


def test_twenty_successful_bookings_then_limit(client):
    store = client.app.state.deps.store
    template = store.doctors[0]
    store.doctors = [{**template, "slot_id": f"load-slot-{i}"} for i in range(21)]
    for i in range(21):
        out = start(client)
        response = client.post(f"/w1/{out['thread_id']}/resume", headers=headers(),
                               json={"value": out["interrupt"]["options"][0]["slot_id"]})
        if i < 20:
            assert response.status_code == 200 and response.json()["booking_id"]
        else:
            assert response.status_code == 429 and response.headers["Retry-After"]
    assert len(store.booked) == 20


def test_other_patient_from_other_address_unaffected(client, monkeypatch):
    monkeypatch.setenv("RL_READ_PER_MIN", "1")
    app = create_app(build_test_deps)
    with TestClient(app, client=("192.0.2.1", 1000)) as first:
        assert first.get("/records/access-log", headers=headers()).status_code == 200
        assert first.get("/records/access-log", headers=headers()).status_code == 429
        with TestClient(app, client=("192.0.2.2", 1000)) as second:
            assert second.get("/records/access-log", headers=headers("PT-B")).status_code == 200


def test_forged_forwarded_for_does_not_change_direct_client_bucket(monkeypatch):
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    monkeypatch.setenv("RL_READ_PER_MIN", "1")
    app = create_app(build_test_deps)
    with TestClient(app, client=("192.0.2.10", 1000)) as first:
        assert first.get(
            "/records/access-log",
            headers={**headers(), "X-Forwarded-For": "198.18.0.1"},
        ).status_code == 200
        assert first.get(
            "/records/access-log",
            headers={**headers(), "X-Forwarded-For": "198.18.0.2"},
        ).status_code == 429
        with TestClient(app, client=("192.0.2.11", 1000)) as second:
            assert second.get(
                "/records/access-log",
                headers={**headers("PT-B"), "X-Forwarded-For": "198.18.0.1"},
            ).status_code == 200


def test_patient_and_ip_buckets(monkeypatch):
    monkeypatch.setenv("RL_READ_PER_MIN", "2")
    monkeypatch.setenv("RL_BOOK_PER_HOUR", "1")
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert limiter.check("A", "ip1", "read") == 0
    assert limiter.check("A", "ip1", "read") == 0
    assert limiter.check("A", "ip2", "read") == 30
    assert limiter.check("B", "ip2", "read") == 0
    assert limiter.check("B", "ip1", "read") == 30
    assert limiter.check("A", "ip1", "book") == 0
    assert limiter.check("A", "ip2", "book") == 3600
    now[0] = 30
    assert limiter.check("A", "ip1", "read") == 0


def test_json_logs_exclude_symptoms(client, caplog):
    symptom = "crushing chest pain and sweating"
    with caplog.at_level("INFO", logger="sahyog.api"):
        response = client.post("/w1/triage", headers=headers(), json={"text": symptom})
    records = [json.loads(r.message) for r in caplog.records if r.name == "sahyog.api"]
    assert len(records) == 1
    row = records[0]
    assert symptom not in json.dumps(row)
    assert row["user"] == hashlib.sha256(b"PT-A").hexdigest()[:12]
    assert row["trace_id"] == response.headers["X-Trace-Id"]
    assert {"ts", "method", "path", "status", "latency_ms", "thread_id", "route"} <= row.keys()


def test_model_outage_sets_degraded_header_and_log(client, caplog, monkeypatch):
    deps = client.app.state.deps
    deps.w1_llm.responses = {
        "w1.red_flag_net": ModelUnavailable("offline"),
        "w1.match": ModelUnavailable("offline"),
    }
    monkeypatch.setenv("W1_FRONT_DESK_SLA_MINUTES", "30")
    with caplog.at_level("INFO", logger="sahyog.api"):
        response = client.post("/w1/triage", headers=headers(),
                               json={"text": "lower back pain for three weeks"})
    assert response.status_code == 200
    assert response.headers["X-Degraded"] == "true"
    assert response.json()["route"] == "front_desk" and response.json()["booking_id"] is None
    row = next(json.loads(r.message) for r in caplog.records
               if r.name == "sahyog.api" and json.loads(r.message).get("path") == "/w1/triage")
    assert row["degraded"] is True
    assert deps.store.events[-1]["detail"]["reason"] == "model unavailable"

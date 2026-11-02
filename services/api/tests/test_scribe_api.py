from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.deps import build_test_deps
from services.api.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    with TestClient(create_app(build_test_deps)) as test_client:
        yield test_client


def doctor_headers(doctor_id="DR-W3-TEST", key=None):
    headers = {"Authorization": f"Bearer demo-doc-{doctor_id}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def create_draft(client, key=None):
    return client.post(
        "/scribe/api/drafts",
        headers=doctor_headers(key=uuid4().hex if key is None else key),
        json={"encounter_id": "ENC-W3-TEST", "transcript": "Continue metformin 500 mg daily."},
    )


def test_scribe_shell_is_public_and_utf8(client):
    response = client.get("/scribe")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "Approve &amp; sign" not in response.text
    assert "out of stock at" in response.text
    assert "GROUNDING REVIEW" in response.text


def test_scribe_api_requires_doctor_role_and_idempotency_key(client):
    assert client.get("/scribe/api/drafts").status_code == 401
    assert client.get("/scribe/api/drafts", headers={"Authorization": "Bearer demo-PT1"}).status_code == 403
    assert client.get("/scribe/api/drafts", headers={"Authorization": "Bearer demo-ops-operator1"}).status_code == 403
    assert create_draft(client, key="").status_code == 400
    assert create_draft(client, key="short").status_code == 400


def test_scribe_create_replay_ownership_list_and_signed_history(client):
    key = uuid4().hex
    response = create_draft(client, key)
    assert response.status_code == 200, response.text
    created = response.json()
    draft = created["draft"]
    assert draft["status"] == "draft"
    assert created["flags"] == []
    assert draft["doctor_id"] == "DR-W3-TEST"
    assert draft["soap_json"]["medicines"][0]["status"] == "in_stock"

    replay = create_draft(client, key)
    assert replay.status_code == 200
    assert replay.json() == created
    changed = client.post(
        "/scribe/api/drafts",
        headers=doctor_headers(key=key),
        json={"encounter_id": "ENC-W3-TEST", "transcript": "A different transcript."},
    )
    assert changed.status_code == 409

    draft_path = f"/scribe/api/drafts/{draft['draft_id']}"
    assert client.get(draft_path, headers=doctor_headers("DR-OTHER")).status_code == 404
    assert client.get("/scribe/api/drafts?status=draft", headers=doctor_headers()).json()[0]["draft_id"] == draft["draft_id"]
    assert client.get("/scribe/api/drafts?status=invalid", headers=doctor_headers()).status_code == 422

    edited = client.post(
        f"{draft_path}/review",
        headers=doctor_headers(key=uuid4().hex),
        json={"action": "edit", "edits": [{"field": "P", "value": "Continue metformin 500 mg daily after meals."}]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["draft"]["soap_json"]["P"].endswith("after meals.")
    assert edited.json()["draft"]["edit_history"][0]["field"] == "P"

    signed_key = uuid4().hex
    signed = client.post(
        f"{draft_path}/review",
        headers=doctor_headers(key=signed_key),
        json={"action": "approve"},
    )
    assert signed.status_code == 200, signed.text
    note = signed.json()
    assert note["status"] == "signed"
    assert note["note_id"] and note["version"] == 1
    assert len(note["content_hash"]) == 64
    assert note["signed_note"]["edit_history"][0]["field"] == "P"
    assert client.post(
        f"{draft_path}/review",
        headers=doctor_headers(key=signed_key),
        json={"action": "approve"},
    ).json() == note
    assert client.get(f"{draft_path}", headers=doctor_headers()).json()["signed_note"]["content_hash"] == note["content_hash"]
    assert client.get("/scribe/api/drafts?status=draft", headers=doctor_headers()).json() == []
    assert client.get("/scribe/api/drafts?status=parked", headers=doctor_headers()).json() == []


def test_scribe_reject_and_ops_signed_note_tiles(client):
    assert not {tile["id"] for tile in client.get(
        "/ops/api/summary", headers={"Authorization": "Bearer demo-ops-operator1"}
    ).json()["tiles"]} & {"scribe-review-time", "scribe-edit-distance"}

    created = create_draft(client).json()["draft"]
    rejected = client.post(
        f"/scribe/api/drafts/{created['draft_id']}/review",
        headers=doctor_headers(key=uuid4().hex),
        json={"action": "reject"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    signed_draft = create_draft(client).json()["draft"]
    signed = client.post(
        f"/scribe/api/drafts/{signed_draft['draft_id']}/review",
        headers=doctor_headers(key=uuid4().hex),
        json={"action": "approve"},
    )
    assert signed.status_code == 200
    summary = client.get("/ops/api/summary", headers={"Authorization": "Bearer demo-ops-operator1"})
    assert summary.status_code == 200
    tiles = {tile["id"]: tile for tile in summary.json()["tiles"]}
    assert tiles["scribe-review-time"]["value"]["count"] == 1
    assert tiles["scribe-review-time"]["value"]["p95_s"] < 90
    assert tiles["scribe-edit-distance"]["value"]["count"] == 1
    assert all(tiles[key]["source"] for key in ("scribe-review-time", "scribe-edit-distance"))

from __future__ import annotations

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from services.agent import llm as llm_module
from services.agent.llm import ModelUnavailable, OpenRouterLLM


class FakeResponse:
    def __init__(self, content: dict):
        self.content = io.BytesIO(json.dumps(content).encode())
        self.headers = {"x-openrouter-provider": "fake-provider"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.content.read()


def response(model: str) -> FakeResponse:
    return FakeResponse({
        "provider": f"{model}-provider",
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    })


@pytest.fixture(autouse=True)
def clear_breakers(monkeypatch):
    llm_module._reset_circuit_breakers()
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL_W1", "backup")
    monkeypatch.setenv("LLM_DEADLINE_S_W1", "0.5")
    monkeypatch.setenv("LLM_HEDGE_AFTER_S", "0.05")
    yield
    llm_module._reset_circuit_breakers()


def make_client(**kwargs) -> OpenRouterLLM:
    return OpenRouterLLM(model="primary", workflow="w1", api_key="test-key",
                         max_retries=0, timeout_s=1, **kwargs)


def fail(status=503):
    return HTTPError(llm_module.OPENROUTER_URL, status, "unavailable", None, io.BytesIO(b"down"))


def test_workflow_deadline_and_hedge_defaults(monkeypatch):
    for name in ("LLM_DEADLINE_S_W1", "LLM_DEADLINE_S_W2", "LLM_HEDGE_AFTER_S"):
        monkeypatch.delenv(name, raising=False)
    assert llm_module._workflow_seconds("LLM_DEADLINE_S", "w1", 3.5) == 3.5
    assert llm_module._workflow_seconds("LLM_DEADLINE_S", "w2", 6.0) == 6.0
    assert llm_module._env_seconds("LLM_HEDGE_AFTER_S", 1.8) == 1.8
    assert llm_module._env_seconds("LLM_HEDGE_AFTER_S", 3.0) == 3.0


def test_slow_primary_is_hedged_and_scrubbed_hedge_wins(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()
    sent = []

    def fake_urlopen(req, timeout):
        nonlocal active, max_active
        body = json.loads(req.data)
        with lock:
            active += 1
            max_active = max(max_active, active)
            sent.append(body)
        try:
            if body["model"] == "primary":
                time.sleep(0.16)
            return response(body["model"])
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    llm = make_client()
    started = time.monotonic()
    assert llm.complete_json("system", "Call 98765 43210", purpose="w1.match", trace_id="hedge-test") == {"ok": True}
    elapsed = time.monotonic() - started

    assert elapsed < 0.14
    assert max_active <= 2
    assert {row["model"] for row in sent} == {"primary", "backup"}
    assert all("98765" not in row["messages"][1]["content"] for row in sent)
    assert all("<PHONE_1>" in row["messages"][1]["content"] for row in sent)
    assert any(call["served_by"] == "hedge" and call["selected"] for call in llm.usage.call_records)
    assert any(call["provider"] == "backup-provider" for call in llm.usage.call_records)


def test_primary_error_uses_fallback(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        if body["model"] == "primary":
            raise fail()
        return response(body["model"])

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    llm = make_client()
    assert llm.complete_json("system", "question", purpose="w1.match", trace_id="fallback-test") == {"ok": True}
    assert [row["model"] for row in sent] == ["primary", "backup"]
    assert sent[0]["provider"]["allow_fallbacks"] is False
    assert sent[1]["provider"]["allow_fallbacks"] is True
    assert llm.usage.call_records[-1]["served_by"] == "fallback"
    assert llm.usage.call_records[-1]["selected"] is True


def test_default_fallback_retries_same_model_with_provider_fallbacks(monkeypatch):
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL_W1")
    sent = []

    def fake_urlopen(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        if not body["provider"]["allow_fallbacks"]:
            raise fail()
        return response(body["model"])

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    llm = make_client()
    assert llm.complete_json("system", "question", purpose="w1.match", trace_id="provider-fallback") == {"ok": True}
    assert [row["model"] for row in sent] == ["primary", "primary"]
    assert [row["provider"]["allow_fallbacks"] for row in sent] == [False, True]


def test_parallel_model_calls_share_the_two_call_request_limit(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_urlopen(req, timeout):
        nonlocal active, max_active
        model = json.loads(req.data)["model"]
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            if model == "primary":
                time.sleep(0.08)
            return response(model)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    llm = make_client()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(
            lambda purpose: llm.complete_json("system", "question", purpose=purpose, trace_id="shared-request"),
            ("w1.red_flag_net", "w1.match")))
    assert results == [{"ok": True}, {"ok": True}]
    assert max_active <= 2


def test_both_calls_fail_and_breaker_opens_then_half_opens(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data)["model"])
        raise fail()

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    llm = make_client()
    for index in range(5):
        with pytest.raises(ModelUnavailable):
            llm.complete_json("system", "question", purpose="w1.match", trace_id=f"breaker-{index}")
    assert sent.count("primary") == 5
    assert llm_module._CIRCUITS[("w1", "primary")].open_until > time.monotonic()

    calls_while_open = len(sent)
    with pytest.raises(ModelUnavailable):
        llm.complete_json("system", "question", purpose="w1.match", trace_id="breaker-open")
    assert len(sent) == calls_while_open

    for circuit in llm_module._CIRCUITS.values():
        circuit.open_until = time.monotonic() - 1
    monkeypatch.setattr(llm_module.urllib.request, "urlopen",
                        lambda req, timeout: response(json.loads(req.data)["model"]))
    assert llm.complete_json("system", "question", purpose="w1.match", trace_id="breaker-probe") == {"ok": True}
    assert llm_module._CIRCUITS[("w1", "primary")].open_until == 0


def test_all_failed_hedges_raise_unavailable(monkeypatch):
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(fail()))
    llm = make_client()
    with pytest.raises(ModelUnavailable, match="all model calls failed"):
        llm.complete_json("system", "question", purpose="w1.match", trace_id="both-fail")

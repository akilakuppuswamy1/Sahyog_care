"""Synthetic-only regressions for the production egress boundary."""
import json
import time

import pytest

from services.common import deid
from services.agent import llm as module
from services.common.tracing import InMemoryExporter, Tracer
from services.agent.tests.test_llm import FakeResponse
from tools.egress_replay import CLEAN, enforce_zero_hits, generate, replay


def test_generator_deterministic_and_zero_hit_gate():
    assert generate() == generate()
    assert generate(2) != generate()
    with pytest.raises(RuntimeError, match="trigger c"):
        enforce_zero_hits({"total_hits": 1, "refused": 0})


def test_offline_replay_and_clinical_numbers(monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        pytest.fail("offline replay attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    report = replay()
    enforce_zero_hits(report)
    assert all(value == 1 for value in report["recall"].values())
    assert report["false_positive_rate"] == 0
    for text in CLEAN:
        assert deid.prepare_egress("", text)[1] == text
    # Warm-model per-call budget; cold load is deliberately outside request handling.
    assert report["deid_ms_p95"] < 50, report


def test_verhoeff_and_refuse_survivor(monkeypatch):
    assert deid.verhoeff_valid("234567890124")
    assert not deid.verhoeff_valid("234567890123")
    monkeypatch.setattr(deid, "deidentify", lambda text, mapping: text)
    with pytest.raises(module.ModelError, match="refusing to send"):
        deid.prepare_egress("", "Call +91 9876543210")


def test_name_context_does_not_remove_symptoms_and_named_dob_is_removed():
    text = "I am having chest pain and sweating."
    assert deid.prepare_egress("", text)[1] == text
    _, outbound, mapping, _ = deid.prepare_egress("", "My name is Priya Nair. DOB: 12 January 1980.")
    assert "Priya Nair" not in outbound
    assert "12 January 1980" not in outbound
    assert deid.reidentify(outbound, mapping) == "My name is Priya Nair. DOB: 12 January 1980."


@pytest.mark.parametrize("workflow", ["w1", "w2", "w3"])
def test_production_transport_hedge_restoration_and_spans(monkeypatch, workflow):
    module._reset_circuit_breakers()
    monkeypatch.setenv(f"LLM_HEDGE_AFTER_S_{workflow.upper()}", "0.03")
    monkeypatch.setenv(f"LLM_DEADLINE_S_{workflow.upper()}", "2")
    monkeypatch.setenv(f"OPENROUTER_FALLBACK_MODEL_{workflow.upper()}", "backup")
    sent = []
    def transport(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        if body["model"] == "primary":
            time.sleep(0.15)
        return FakeResponse({"choices": [{"message": {"content": json.dumps({"reply": "<PHONE_1>", "nested": ["<EMAIL_1>"]})}}]})
    monkeypatch.setattr(module.urllib.request, "urlopen", transport)
    exporter = InMemoryExporter()
    client = module.OpenRouterLLM(model="primary", api_key="synthetic-test", workflow=workflow,
                                  max_retries=0, tracer=Tracer(exporter))
    reply = client.complete_json("Email synthetic@example.invalid", "Call +91 9876543210",
                                 purpose=f"{workflow}.test", trace_id=f"synthetic-{workflow}")
    assert reply == {"reply": "+91 9876543210", "nested": ["synthetic@example.invalid"]}
    time.sleep(0.17)
    assert len(sent) == 2
    for body in sent:
        assert "9876543210" not in json.dumps(body)
        assert "synthetic@example.invalid" not in json.dumps(body)
    assert len(exporter.spans) == 2
    assert all(isinstance(span.deid_ms, (float, int)) for span in exporter.spans)
    assert "9876543210" not in repr(exporter.spans)


def test_invalid_aadhaar_refused_without_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("transport must never see a surviving identifier")
    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden)
    client = module.OpenRouterLLM(model="synthetic", api_key="synthetic-test", max_retries=0)
    with pytest.raises(module.ModelError):
        client.complete_json("", "Aadhaar 234567890123", purpose="w1.test", trace_id="synthetic-refusal")

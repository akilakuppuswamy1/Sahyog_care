"""Synthetic-only regressions for the production egress boundary."""
import json
import time

import pytest

from services.common import deid
from services.agent import llm as module
from services.common.tracing import InMemoryExporter, Tracer
from services.agent.tests.test_llm import FakeResponse
from tools.egress_replay import CLEAN, enforce_latency, enforce_zero_hits, generate, replay


def test_generator_deterministic_and_zero_hit_gate():
    assert generate() == generate()
    assert generate(2) != generate()
    with pytest.raises(RuntimeError, match="trigger c"):
        enforce_zero_hits({"total_hits": 1, "refused": 0})
    enforce_latency({"deid_ms_p95": 50})
    with pytest.raises(RuntimeError, match="exceeded 50 ms"):
        enforce_latency({"deid_ms_p95": 50.1})


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
    assert isinstance(report["deid_ms_p95"], (int, float))
    assert isinstance(report["deid_ms_median"], (int, float))


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


def test_analyzer_warmup_runs_once(monkeypatch):
    calls = []

    class FakeAnalyzer:
        def analyze(self, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(deid, "_WARMED", False)
    monkeypatch.setattr(deid, "analyzer", lambda: FakeAnalyzer())

    deid.warm_analyzer()
    deid.warm_analyzer()

    assert len(calls) == 1
    assert calls[0]["text"] == "Sahyog de-identification warm-up."


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

@pytest.mark.parametrize("module_name", [
    "evals.run_w1", "evals.run_w2", "evals.run_w3", "services.records.worker",
])
def test_entrypoints_warm_before_processing(monkeypatch, module_name):
    import importlib
    import dotenv

    module = importlib.import_module(module_name)
    calls = []

    class StartupStopped(Exception):
        pass

    def warm():
        calls.append("warm")
        raise StartupStopped

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", [module_name])
    monkeypatch.setattr(deid, "warm_analyzer", warm)
    if hasattr(module, "warm_analyzer"):
        monkeypatch.setattr(module, "warm_analyzer", warm)
    with pytest.raises(StartupStopped):
        module.main()
    assert calls == ["warm"]

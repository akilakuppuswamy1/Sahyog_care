from __future__ import annotations

import csv
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from services.agent.llm import ModelUnavailable, OpenRouterLLM, ScriptedLLM, _workflow_seconds
from services.agent.w3.graph import compile_w3
from services.agent.w3.nodes import PLACEHOLDER, grounding_check, park_expired_reviews
from services.agent.w3.stock import CsvStockAdapter
from services.agent.w3.store import InMemoryW3Store
from services.common.tracing import InMemoryExporter, Tracer


def stock_csv(path, rows=None):
    rows = rows or [
        {"medicine_id": "MED1", "clinic_id": "PUN-H01", "name": "Atorva",
         "composition": "atorvastatin", "price_inr": "10", "qty": "0",
         "as_of": "2026-11-02", "source": "csv"},
        {"medicine_id": "MED2", "clinic_id": "PUN-H01", "name": "Atorlip",
         "composition": "atorvastatin", "price_inr": "12", "qty": "8",
         "as_of": "2026-11-02", "source": "csv"},
        {"medicine_id": "MED3", "clinic_id": "PUN-H01", "name": "Metformin",
         "composition": "metformin", "price_inr": "5", "qty": "10",
         "as_of": "2026-11-02", "source": "csv"},
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return CsvStockAdapter(path)


def medicine_response():
    return {
        "S": "Follow-up visit.",
        "O": "No new findings.",
        "A": "Assessment as dictated.",
        "P": "Start Atorva 10 mg at night.",
        "medicines": [{"name": "Atorva", "molecule": "atorvastatin", "dose": "10 mg",
                       "frequency": "at night", "quote": "Atorva 10 mg at night"}],
    }


def graph_for(tmp_path, responses, tracer=None):
    stock = stock_csv(tmp_path / "stock.csv")
    store = InMemoryW3Store({
        "E1": {"encounter_id": "E1", "patient_id": "PT-SYN-1", "doctor_id": "DR1",
               "clinic_id": "PUN-H01"},
    })
    llm = ScriptedLLM(responses, tracer=tracer)
    graph = compile_w3(llm, store, stock, InMemorySaver(), tracer)
    return graph, llm, store, stock


def start(graph, text="Doctor: Atorva 10 mg at night.", thread=None):
    config = {"configurable": {"thread_id": thread or uuid.uuid4().hex}}
    output = graph.invoke({"transcript": text, "encounter_id": "E1", "doctor_id": "DR1"}, config)
    return output, config


def review(output):
    pending = output.get("__interrupt__") or []
    assert pending
    return pending[0].value


def test_grounding_replaces_unsupported_molecule_and_class_only_mention(tmp_path):
    stock = stock_csv(tmp_path / "stock.csv")
    state = {
        "transcript": "Doctor: Start a statin 20 mg at night.",
        "soap_json": {"S": "", "O": "", "A": "", "P": "", "medicines": []},
        "medicines": [{"name": "atorvastatin", "molecule": "atorvastatin", "dose": "20 mg",
                       "frequency": "at night", "quote": "statin 20 mg at night"}],
        "flags": [],
    }
    checked = grounding_check(state, stock)
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert any(flag["kind"] == "grounding" for flag in checked["flags"])
    assert sum(item["name"].lower() == "statin" for item in checked["medicines"]) == 1


def test_grounding_uses_the_final_corrected_dose(tmp_path):
    stock = stock_csv(tmp_path / "stock.csv")
    transcript = "Doctor: metformin 500 mg twice daily—sorry, make that 250 mg twice daily."
    common = {"name": "metformin", "molecule": "metformin",
              "frequency": "twice daily", "quote": transcript.split("Doctor: ", 1)[1]}
    state = {"transcript": transcript, "soap_json": {"S": "", "O": "", "A": "", "P": ""},
             "medicines": [{**common, "dose": "500 mg"}], "flags": []}
    old = grounding_check(state, stock)
    assert old["medicines"][0]["dose"] == PLACEHOLDER
    state["medicines"] = [{**common, "dose": "250 mg"}]
    corrected = grounding_check(state, stock)
    assert corrected["medicines"][0]["dose"] == "250 mg"
    assert not any(flag["kind"] == "grounding" for flag in corrected["flags"])


def test_stock_only_lists_in_stock_same_molecule_alternatives(tmp_path):
    stock = stock_csv(tmp_path / "stock.csv")
    result = stock.check("PUN-H01", {"name": "Atorva", "molecule": "atorvastatin"})
    assert result["status"] == "out_of_stock"
    assert result["as_of"] == "2026-11-02"
    assert [item["name"] for item in result["alternatives"]] == ["Atorlip"]
    assert all(item["composition"] == "atorvastatin" and item["suggestion_only"]
               for item in result["alternatives"])
    # A brand the clinic does not stock cannot be matched unambiguously: unknown, not out of stock.
    unlisted = stock.check("PUN-H01", {"name": "Unlisted brand", "molecule": "atorvastatin"})
    assert unlisted["status"] == "unknown"
    assert unlisted["medicine_id"] is None
    assert unlisted["alternatives"] == []


def test_class_only_draft_shows_unknown_stock_and_blocks_signing_until_resolved(tmp_path):
    response = {"S": "", "O": "", "A": "", "P": "Start a statin.",
                "medicines": [{"name": "statin", "molecule": "", "dose": "", "frequency": "",
                               "quote": "Start a statin"}]}
    graph, _, store, _ = graph_for(tmp_path, {"w3.draft_soap": response})
    output, config = start(graph, "Doctor: Start a statin.")
    item = review(output)["draft"]["medicines"][0]
    assert (item["name"], item["molecule"], item["status"]) == ("statin", PLACEHOLDER, "unknown")
    assert "stock_status" not in item
    assert {flag["kind"] for flag in review(output)["flags"]} >= {"molecule_unresolved"}

    blocked = graph.invoke(Command(resume={"action": "approve"}), config)
    assert review(blocked)["error"] == "Resolve grounding flags before signing."
    assert not store.notes


def test_doctor_can_sign_stock_flagged_note_and_note_hash_is_present(tmp_path):
    graph, _, store, _ = graph_for(tmp_path, {"w3.draft_soap": medicine_response()})
    output, config = start(graph)
    payload = review(output)
    assert any(flag["kind"] == "stock" for flag in payload["flags"])
    assert payload["draft"]["medicines"][0]["alternatives"][0]["name"] == "Atorlip"

    signed = graph.invoke(Command(resume={"action": "approve"}), config)
    assert signed.get("__interrupt__") is None
    assert len(store.notes) == 1
    note = store.notes[0]
    assert note["signed_by"] == "DR1"
    assert len(note["content_hash"]) == 64
    assert note["edit_distance_by_field"] == {}
    assert store.drafts[payload["draft_id"]]["status"] == "signed"
    assert signed["notification"]["type"] == "clinical_note_signed"


def test_later_edit_creates_version_two_with_supersedes(tmp_path):
    graph, _, store, _ = graph_for(tmp_path, {"w3.draft_soap": medicine_response()})
    first, first_config = start(graph, thread="first-note")
    first_signed = graph.invoke(Command(resume={"action": "approve"}), first_config)
    second, second_config = start(graph, thread="second-note")

    edited = graph.invoke(Command(resume={"action": "edit", "edits": [
        {"field": "P", "value": "Continue Atorva 10 mg at night."},
    ]}), second_config)
    assert review(edited)["draft"]["P"] == "Continue Atorva 10 mg at night."
    second_signed = graph.invoke(Command(resume={"action": "approve"}), second_config)

    assert len(store.notes) == 2
    assert store.notes[1]["version"] == 2
    assert store.notes[1]["supersedes"] == first_signed["note"]["note_id"]
    assert store.notes[1]["edit_distance_by_field"]["P"] > 0
    assert len(store.edits) == 1
    assert store.edits[0]["field"] == "P"


def test_unauthorized_doctor_is_rejected_before_model_call(tmp_path):
    graph, llm, _, _ = graph_for(tmp_path, {"w3.draft_soap": medicine_response()})
    with pytest.raises(PermissionError, match="treating doctor"):
        graph.invoke({"transcript": "Doctor: routine follow-up.", "encounter_id": "E1", "doctor_id": "DR2"},
                     {"configurable": {"thread_id": "wrong-doctor"}})
    assert llm.sent == []


def test_model_outage_parks_an_empty_unsigned_draft(tmp_path):
    graph, _, store, _ = graph_for(tmp_path, {"w3.draft_soap": ModelUnavailable("offline")})
    output, _ = start(graph)
    assert output.get("__interrupt__") is None
    assert not store.notes
    draft = next(iter(store.drafts.values()))
    assert draft["status"] == "parked"
    assert draft["soap_json"] == {"S": "", "O": "", "A": "", "P": "", "medicines": []}
    assert draft["flags"] == [{"kind": "model_unavailable", "message": "model unavailable"}]


def test_rules_block_model_call_and_transcript_is_scrubbed_before_egress(tmp_path):
    tracer = Tracer(InMemoryExporter())
    graph, llm, store, _ = graph_for(
        tmp_path, {"w3.draft_soap": medicine_response()}, tracer
    )
    emergency, _ = start(graph, "Doctor: Patient reports chest pain with sweating.")
    assert emergency["route"] == "parked"
    assert llm.sent == []
    assert not store.notes

    output, _ = start(graph, "Doctor: Patient number 9876543210; routine Atorva 10 mg at night.")
    assert review(output)["type"] == "doctor_review"
    sent = llm.sent[0][1]
    assert "9876543210" not in sent
    assert "<PHONE_1>" in sent
    assert "routine Atorva" not in repr(tracer.exporter.spans)
    assert "9876543210" not in repr(tracer.exporter.spans)


def test_timeout_worker_parks_stale_review(tmp_path, monkeypatch):
    monkeypatch.setenv("W3_REVIEW_TIMEOUT_MIN", "1")
    _, _, store, _ = graph_for(tmp_path, {"w3.draft_soap": medicine_response()})
    output, _ = start(compile_w3(ScriptedLLM({"w3.draft_soap": medicine_response()}), store,
                                 stock_csv(tmp_path / "stock2.csv"), InMemorySaver()))
    draft_id = review(output)["draft_id"]
    store.drafts[draft_id]["review_started_at"] = datetime.now(timezone.utc) - timedelta(minutes=2)
    assert park_expired_reviews(store) == [draft_id]
    assert store.drafts[draft_id]["status"] == "parked"


def test_w3_model_and_deadline_defaults_do_not_change_other_workflows(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_W3", raising=False)
    monkeypatch.delenv("LLM_DEADLINE_S_W3", raising=False)
    monkeypatch.delenv("LLM_DEADLINE_S_W1", raising=False)
    monkeypatch.delenv("LLM_DEADLINE_S_W2", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "local-test-key")
    client = OpenRouterLLM(workflow="w3")
    assert client.model == "qwen/qwen3-235b-a22b-2507"
    assert _workflow_seconds("LLM_DEADLINE_S", "w3", 20.0) == 20.0
    assert _workflow_seconds("LLM_DEADLINE_S", "w1", 8.0) == 8.0
    assert _workflow_seconds("LLM_DEADLINE_S", "w2", 6.0) == 6.0

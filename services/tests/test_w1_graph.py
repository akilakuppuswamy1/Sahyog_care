"""W1 end to end through LangGraph with an in-memory checkpointer and a scripted model.

Skips when langgraph is not installed. On the pod machine it runs as part of `pytest`.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("langgraph")
from langgraph.types import Command  # noqa: E402

try:
    from langgraph.checkpoint.memory import InMemorySaver as Saver  # noqa: E402
except ImportError:  # older name
    from langgraph.checkpoint.memory import MemorySaver as Saver  # noqa: E402

from services.agent.llm import ScriptedLLM  # noqa: E402
from services.agent.store import InMemoryStore  # noqa: E402
from services.agent.w1.graph import compile_w1  # noqa: E402

MATCH_ORTHO = {"specialty": "SP07", "alternative": "SP08", "confidence": 0.82, "clarifying_question": None}
NET_CLEAR = {"red_flag": False, "flag": ""}


def run(graph, payload, thread):
    cfg = {"configurable": {"thread_id": thread}}
    out = graph.invoke(payload, cfg)
    intr = out.get("__interrupt__") or []
    return out, (intr[0].value if intr else None), cfg


def make(responses):
    llm, store = ScriptedLLM(responses), InMemoryStore.demo()
    return compile_w1(llm, store, Saver()), llm, store


def test_routine_case_pauses_for_the_pick_then_books_on_resume():
    g, llm, store = make({"w1.red_flag_net": NET_CLEAR, "w1.match": MATCH_ORTHO})
    out, pause, cfg = run(g, {"text": "Dull lower back pain for three weeks, worse after sitting.", "age": 41,
                              "patient_id": "PT0001"}, uuid.uuid4().hex)
    assert pause["type"] == "pick_slot" and len(pause["options"]) == 3
    assert "booking_id" not in out or out["booking_id"] is None        # nothing booked before the human picks
    slot = pause["options"][1]["slot_id"]
    done = g.invoke(Command(resume=slot), cfg)
    assert done["booking_id"] and done["chosen_slot"] == slot
    assert store.booked[slot] == done["booking_id"]
    assert [e["resource_type"] for e in store.events] == ["appointment", "sms_outbox"]


def test_red_flag_ends_the_run_with_no_model_call_and_no_slot():
    g, llm, store = make({"w1.red_flag_net": NET_CLEAR, "w1.match": MATCH_ORTHO})
    out, pause, _ = run(g, {"text": "papa ko seene mein bahut dard ho raha hai aur pasina aa raha hai", "age": 58,
                            "patient_id": "PT0001"}, uuid.uuid4().hex)
    assert pause is None and "112" in out["reply"]
    assert llm.sent == [] and store.booked == {}
    assert store.events[0]["action"] == "escalate"


def test_red_flag_in_a_clarification_answer_is_caught_by_the_rules():
    """The clarify edge goes back through intake, so new information meets the rules first."""
    g, llm, store = make({"w1.red_flag_net": NET_CLEAR,
                          "w1.match": {"specialty": "SP01", "confidence": 0.3, "clarifying_question": "Where is the pain?"}})
    _, pause, cfg = run(g, {"text": "not feeling well", "age": 60, "patient_id": "PT0001"}, uuid.uuid4().hex)
    assert pause["type"] == "clarify"
    sent_before = len(llm.sent)
    out = g.invoke(Command(resume="crushing chest pain and I am sweating"), cfg)
    assert "112" in out["reply"] and out.get("__interrupt__") is None
    assert len(llm.sent) == sent_before                                 # rules fired before any model call


def test_two_vague_turns_hand_off_to_front_desk():
    g, llm, store = make({"w1.red_flag_net": NET_CLEAR,
                          "w1.match": {"specialty": "SP01", "confidence": 0.2, "clarifying_question": "Can you say more?"}})
    _, pause, cfg = run(g, {"text": "pain", "age": None, "patient_id": "PT0001"}, uuid.uuid4().hex)
    assert pause["type"] == "clarify"
    out = g.invoke(Command(resume="it hurts"), cfg)
    assert out.get("__interrupt__") is None and out["route"] == "front_desk"
    assert store.events[-1]["purpose"] == "front_desk"


def test_resume_after_restart_uses_the_checkpoint():
    """Same checkpointer, a freshly compiled graph: any replica can resume any thread (deck p.36)."""
    llm, store, saver = ScriptedLLM({"w1.red_flag_net": NET_CLEAR, "w1.match": MATCH_ORTHO}), InMemoryStore.demo(), Saver()
    g1 = compile_w1(llm, store, saver)
    _, pause, cfg = run(g1, {"text": "knee pain climbing stairs for months", "age": 61, "patient_id": "PT0001"},
                        uuid.uuid4().hex)
    g2 = compile_w1(llm, store, saver)
    done = g2.invoke(Command(resume=pause["options"][0]["slot_id"]), cfg)
    assert done["booking_id"]

"""W1 node logic without LangGraph or a network. Each test is one promise from the pack."""
from __future__ import annotations

import pytest

from services.agent.llm import ModelError, ScriptedLLM
from services.agent.store import InMemoryStore
from services.agent.w1 import matcher, nodes as n

MATCH_ORTHO = {"specialty": "SP07", "alternative": "SP08", "confidence": 0.82, "clarifying_question": None}
NET_CLEAR = {"red_flag": False, "flag": ""}


def start(text: str, age=40, **kw) -> dict:
    s = {"text": text, "age": age, "patient_id": "PT0001", **kw}
    for step in (n.intake, n.detect_language, n.red_flag_rules):
        s |= step(s)
    return s


def test_red_flag_hands_off_before_any_model_call():
    llm = ScriptedLLM({"w1.red_flag_net": NET_CLEAR, "w1.match": MATCH_ORTHO})
    store = InMemoryStore.demo()
    s = start("crushing chest pain and sweating", 55)
    assert n.after_red_flags(s) == "handoff"
    s |= n.er_handoff(s, store)
    assert llm.sent == []                                     # no model touched this patient
    assert "112" in s["reply"] and s["specialty"] is None and s["candidates"] == []
    assert store.events[-1]["action"] == "escalate" and store.events[-1]["purpose"] == "emergency_desk"


def test_model_net_can_add_a_flag():
    llm = ScriptedLLM({"w1.red_flag_net": {"red_flag": True, "flag": "possible sepsis"}})
    s = start("very high fever, shivering and confused since last night", 70)
    assert n.after_red_flags(s) == "continue"
    s |= n.red_flag_net(s, llm)
    assert n.after_red_flags(s) == "handoff" and s["red_flags"] == ["model:possible_sepsis"]


def test_broken_model_net_never_blocks_or_clears():
    llm = ScriptedLLM({"w1.red_flag_net": ModelError("down")})
    s = start("lower back pain for three weeks")
    s |= n.red_flag_net(s, llm)
    assert s["red_flags"] == [] and n.after_red_flags(s) == "continue"


def test_model_checks_runs_net_and_match_and_a_net_flag_wins():
    llm = ScriptedLLM({
        "w1.red_flag_net": {"red_flag": True, "flag": "possible sepsis"},
        "w1.match": MATCH_ORTHO,
    })
    s = start("lower back pain for three weeks")
    s |= n.model_checks(s, llm)
    assert {purpose for purpose, _ in llm.sent} == {"w1.red_flag_net", "w1.match"}
    assert n.after_model_checks(s) == "handoff"
    assert s["red_flags"] == ["model:possible_sepsis"]
    assert s["specialty"] is None and s["alternative"] is None


def test_model_checks_survives_a_failed_net():
    llm = ScriptedLLM({"w1.red_flag_net": ModelError("down"), "w1.match": MATCH_ORTHO})
    s = start("lower back pain for three weeks")
    s |= n.model_checks(s, llm)
    assert {purpose for purpose, _ in llm.sent} == {"w1.red_flag_net", "w1.match"}
    assert n.after_model_checks(s) == "book"
    assert s["red_flags"] == [] and s["specialty"] == "SP07"


def test_routine_match_shortlists_three_with_language_preference():
    llm = ScriptedLLM({"w1.match": MATCH_ORTHO})
    store = InMemoryStore.demo()
    s = start("kamar mein dard hai teen hafte se", 41)
    assert s["language"] == "hi-Latn"
    s |= n.match_specialty(s, llm)
    assert n.after_match(s) == "book"
    s |= n.shortlist(s, store)
    assert len(s["candidates"]) == 3
    assert "hi" in s["candidates"][0]["languages"]           # Hindi-speaking doctor first
    payload = n.options_payload(s)
    assert payload["type"] == "pick_slot" and len(payload["options"]) == 3


def test_invented_specialty_is_low_confidence_not_a_guess():
    m = matcher.validate_match({"specialty": "SP99", "confidence": 0.95})
    assert not m.valid and m.confidence == 0.0


def test_two_low_confidence_turns_go_to_front_desk():
    llm = ScriptedLLM({"w1.match": {"specialty": "SP01", "confidence": 0.3, "clarifying_question": "Where does it hurt?"}})
    store = InMemoryStore.demo()
    s = start("not feeling well")
    s |= n.match_specialty(s, llm)
    assert s["route"] == "clarify" and s["question"] == "Where does it hurt?"
    s |= n.apply_clarification(s, "everywhere")
    s |= n.match_specialty(s, llm)
    assert s["route"] == "front_desk"
    s |= n.front_desk(s, store)
    assert store.events[-1]["purpose"] == "front_desk"


def test_clarifying_question_that_names_a_disease_is_replaced():
    m = matcher.validate_match({"specialty": "SP01", "confidence": 0.2,
                                "clarifying_question": "It sounds like gastritis. How long have you had it?"})
    assert m.clarifying_question == matcher.FALLBACK_QUESTION


def test_patient_can_only_pick_an_offered_slot():
    s = {"candidates": [{"slot_id": "A"}, {"slot_id": "B"}]}
    assert n.apply_choice(s, "B") == {"chosen_slot": "B"}
    with pytest.raises(ValueError):
        n.apply_choice(s, "DR9999-20261116-0900")


def test_booking_is_idempotent_and_a_taken_slot_retries_then_hands_off():
    store = InMemoryStore.demo()
    s = {"trace_id": "t1", "patient_id": "PT0001", "chosen_slot": store.doctors[0]["slot_id"],
         "candidates": store.doctors[:3], "book_attempts": 0}
    first = n.book(s, store)["booking_id"]
    assert n.book(s, store)["booking_id"] == first           # retry of the same run = same booking
    other = {**s, "trace_id": "t2", "patient_id": "PT0002"}
    upd = n.book(other, store)
    assert upd["booking_id"] is None and n.after_book({**other, **upd}) == "shortlist"
    assert n.after_book({**other, "booking_id": None, "book_attempts": 2}) == "front_desk"


def test_egress_scrubs_phone_and_email_before_the_model_sees_them():
    llm = ScriptedLLM({"w1.match": MATCH_ORTHO})
    s = start("back pain, call me on 98765 43210 or priya@example.com")
    n.match_specialty(s, llm)
    sent = llm.sent[-1][1]
    assert "98765" not in sent and "@example.com" not in sent and "<PHONE_1>" in sent

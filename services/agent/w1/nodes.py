"""W1 node logic as plain functions: state in, partial update out.

No LangGraph import here, so every rule is unit-testable without the framework or a model.
graph.py wires these into a StateGraph and adds the two human pauses (clarify, pick).
"""
from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from services.common.timefmt import ist
from services.agent.llm import JsonLLM
from services.agent.store import SlotFull, Store
from services.agent.w1 import matcher, redflags

CONFIDENCE_MIN = 0.6            # deck p.14
MAX_LOW_CONF_TURNS = 2          # two low-confidence turns -> front desk
MAX_BOOK_RETRIES = 2            # slot taken between shortlist and booking

EMERGENCY_REPLY = ("These symptoms need urgent attention now. Please call 112 or go to the nearest "
                   "emergency department. I have alerted Sahyog's emergency desk.")
# "a nurse will call you within two minutes" (pack p.14) is withheld until D2 Q1 / D3 A09 confirm staffing.
CRISIS_REPLY = ("I'm really sorry you're feeling this way, and I'm glad you told me. You don't have to go through "
                "this alone. Please call Tele-MANAS on 14416, a free 24x7 mental health helpline in India. "
                "If you are in immediate danger, call 112. I have also alerted Sahyog's team so someone can reach you.")
FRONT_DESK_REPLY = ("I want to make sure you see the right doctor, so I've passed your request to our front desk. "
                    "They will contact you shortly.")


class W1State(TypedDict, total=False):
    trace_id: str
    idempotency_key: str
    patient_id: str            # from the auth context, never from model output
    text: str                  # everything the patient has told us, joined
    age: int | None
    language: str
    red_flags: list[str]
    route: str | None          # emergency_desk | crisis_line | clarify | front_desk | book
    specialty: str | None
    alternative: str | None
    confidence: float
    low_conf_turns: int
    question: str
    candidates: list[dict]
    chosen_slot: str | None
    book_attempts: int
    booking_id: str | None
    reply: str


def intake(s: W1State) -> W1State:
    text = re.sub(r"\s+", " ", s.get("text", "")).strip()
    return {"text": text, "trace_id": s.get("trace_id") or uuid.uuid4().hex,
            "low_conf_turns": s.get("low_conf_turns", 0), "book_attempts": s.get("book_attempts", 0)}


def detect_language(s: W1State) -> W1State:
    return {"language": matcher.detect_language(s["text"])}


def red_flag_rules(s: W1State) -> W1State:
    r = redflags.evaluate(s["text"], s.get("age"))
    if not r.fired and records_question(s["text"]):
        return {"red_flags": [], "route": "redirect_records"}
    return {"red_flags": list(r.fired), "route": r.route}


SYMPTOMS = re.compile(r"\b(pain|ache|aching|fever|cough|breath|bleed|dizz|vomit|nausea|weak|swell|rash|headache|numb|seizure|faint|suicid|hurt|dard|bukhar|khansi|saans|chakkar|tired|fatigue|itch|diarrh|constipat|palpitation|burning|injur|fracture|sore|depress|anxi|confus|blur|unconscious)\w*\b", re.I)
RECORDS = re.compile(r"\b(hba1c|reports?|records?|x[ -]?ray|blood test|lab results?|haemoglobin|hemoglobin|sgpt)\b", re.I)


def records_question(text):
    return bool(RECORDS.search(text) and not SYMPTOMS.search(text))


def redirect_records(s):
    return {"route": "redirect_records", "reply": "That sounds like a question about your reports. Ask it under My records.", "specialty": None, "candidates": []}


def red_flag_net(s: W1State, llm: JsonLLM) -> W1State:
    extra = matcher.red_flag_net(llm, s["text"], s.get("age"), s["trace_id"])
    merged = redflags.merge(redflags.RedFlagResult(tuple(s.get("red_flags", [])), s.get("route")), extra)
    return {"red_flags": list(merged.fired), "route": merged.route}


def model_checks(s: W1State, llm: JsonLLM) -> W1State:
    with ThreadPoolExecutor(max_workers=2) as executor:
        net_future = executor.submit(red_flag_net, s, llm)
        match_future = executor.submit(match_specialty, s, llm)
        net = net_future.result()
        match = match_future.result()
    if net.get("red_flags"):
        return {**net, "specialty": None, "alternative": None}
    return {**net, **match}


def after_model_checks(s: W1State) -> str:
    return "handoff" if s.get("red_flags") else s["route"]


def after_red_flags(s: W1State) -> str:
    return "handoff" if s.get("red_flags") else ("redirect_records" if s.get("route") == "redirect_records" else "continue")


def er_handoff(s: W1State, tools: Store) -> W1State:
    kind = s.get("route") or "emergency_desk"
    tools.escalate(kind, s["patient_id"], s["trace_id"],
                   {"rules": s["red_flags"], "rules_version": redflags.RULES_VERSION})
    return {"reply": CRISIS_REPLY if kind == "crisis_line" else EMERGENCY_REPLY,
            "specialty": None, "candidates": []}          # must_not: specialty, slot (eval set)


def match_specialty(s: W1State, llm: JsonLLM) -> W1State:
    m = matcher.match_specialty(llm, s["text"], s.get("age"), s["trace_id"])
    if m.valid and m.confidence >= CONFIDENCE_MIN:
        return {"specialty": m.specialty, "alternative": m.alternative, "confidence": m.confidence, "route": "book"}
    turns = s.get("low_conf_turns", 0) + 1
    route = "front_desk" if turns >= MAX_LOW_CONF_TURNS else "clarify"
    return {"specialty": None, "alternative": None, "confidence": m.confidence, "low_conf_turns": turns,
            "question": m.clarifying_question, "route": route}


def after_match(s: W1State) -> str:
    return s["route"]          # "book" | "clarify" | "front_desk"


def apply_clarification(s: W1State, answer: str) -> W1State:
    """The patient's answer is appended and the run goes back through intake, so the red-flag
    rules see the new information before any model does."""
    return {"text": f"{s['text'].rstrip('. ')}. {answer}".strip(), "route": None}


def front_desk(s: W1State, tools: Store) -> W1State:
    tools.escalate("front_desk", s["patient_id"], s["trace_id"],
                   {"reason": "low_confidence" if s.get("low_conf_turns") else "no_candidates",
                    "low_conf_turns": s.get("low_conf_turns", 0)})
    return {"reply": FRONT_DESK_REPLY, "route": "front_desk"}


def shortlist(s: W1State, tools: Store) -> W1State:
    ids = [x for x in (s.get("specialty"), s.get("alternative")) if x]
    cands = tools.shortlist(ids, s.get("language", "en"), n=3)
    return {"candidates": cands, "route": "book" if cands else "front_desk"}


def after_shortlist(s: W1State) -> str:
    return "present" if s.get("candidates") else "front_desk"


def options_payload(s: W1State) -> dict:
    """What the patient sees at the interrupt: options only, no diagnosis."""
    return {"type": "pick_slot",
            "specialty": matcher.SPECIALTIES.get(s["specialty"], s["specialty"]),
            "options": [{k: c[k] for k in ("slot_id", "doctor_name", "clinic", "starts_at", "languages")}
                        for c in s["candidates"]]}


def apply_choice(s: W1State, slot_id: str) -> W1State:
    allowed = {c["slot_id"] for c in s.get("candidates", [])}
    if slot_id not in allowed:          # the patient can only pick what was offered
        raise ValueError(f"slot {slot_id!r} was not one of the offered options")
    return {"chosen_slot": slot_id}


def book(s: W1State, tools: Store) -> W1State:
    key = s.get("idempotency_key") or f"{s['trace_id']}:{s['chosen_slot']}"     # same run + same slot = same booking on retry
    try:
        appt = tools.book(s["patient_id"], s["chosen_slot"], key)
    except SlotFull:
        return {"booking_id": None, "book_attempts": s.get("book_attempts", 0) + 1, "chosen_slot": None}
    tools.audit("write", "appointment", appt, s["patient_id"], "booking", s["trace_id"],
                {"slot_id": s["chosen_slot"], "booked_by": "assistant"})
    return {"booking_id": appt}


def after_book(s: W1State) -> str:
    if s.get("booking_id"):
        return "notify"
    return "front_desk" if s.get("book_attempts", 0) >= MAX_BOOK_RETRIES else "shortlist"


def notify(s: W1State, tools: Store) -> W1State:
    c = next(c for c in s["candidates"] if c["slot_id"] == s["chosen_slot"])
    text = f"Sahyog: your appointment with {c['doctor_name']} at {c['clinic']} is confirmed for {ist(c['starts_at'])}."
    tools.audit("write", "sms_outbox", s["booking_id"], s["patient_id"], "booking_confirmation", s["trace_id"],
                {"template": "booking_confirmed_v1"})     # the SMS text itself goes via the relay, not the audit
    return {"reply": text, "route": "book"}

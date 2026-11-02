"""W1 Find my doctor: the LangGraph wiring (docs/architecture.md §3, ADR-003).

Node order is the safety argument: red-flag rules -> model net (add-only) -> match -> tools -> human pick.
The two human pauses use interrupt(); state survives them in the checkpointer, so the API stays stateless.

Resume values:
  clarify   -> Command(resume="<the patient's answer>")
  pick_slot -> Command(resume="<slot_id from the offered options>")
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from services.agent.llm import JsonLLM
from services.agent.store import Store
from services.agent.w1 import nodes as n
from services.agent.w1.nodes import W1State


def _clarify(s: W1State) -> W1State:
    answer = interrupt({"type": "clarify", "question": s["question"]})
    return n.apply_clarification(s, str(answer))


def _present_and_pick(s: W1State) -> W1State:
    choice = interrupt(n.options_payload(s))
    return n.apply_choice(s, str(choice))


def build_w1(llm: JsonLLM, store: Store) -> StateGraph:
    g = StateGraph(W1State)
    g.add_node("intake", n.intake)
    g.add_node("detect_language", n.detect_language)
    g.add_node("red_flag_rules", n.red_flag_rules)
    g.add_node("model_checks", partial(n.model_checks, llm=llm))
    g.add_node("er_handoff", partial(n.er_handoff, tools=store))
    g.add_node("clarify", _clarify)
    g.add_node("front_desk", partial(n.front_desk, tools=store))
    g.add_node("shortlist", partial(n.shortlist, tools=store))
    g.add_node("present_and_pick", _present_and_pick)
    g.add_node("book", partial(n.book, tools=store))
    g.add_node("notify", partial(n.notify, tools=store))

    g.add_edge(START, "intake")
    g.add_edge("intake", "detect_language")
    g.add_edge("detect_language", "red_flag_rules")
    # Rules first. If any rule fires, no model is ever called on this run.
    g.add_conditional_edges("red_flag_rules", n.after_red_flags,
                            {"handoff": "er_handoff", "continue": "model_checks"})
    g.add_conditional_edges("model_checks", n.after_model_checks,
                            {"handoff": "er_handoff", "book": "shortlist", "clarify": "clarify",
                             "front_desk": "front_desk"})
    g.add_edge("clarify", "intake")            # new information goes through the red-flag rules again
    g.add_conditional_edges("shortlist", n.after_shortlist,
                            {"present": "present_and_pick", "front_desk": "front_desk"})
    g.add_edge("present_and_pick", "book")
    g.add_conditional_edges("book", n.after_book,
                            {"notify": "notify", "shortlist": "shortlist", "front_desk": "front_desk"})
    g.add_edge("er_handoff", END)
    g.add_edge("front_desk", END)
    g.add_edge("notify", END)
    return g


def compile_w1(llm: JsonLLM, store: Store, checkpointer):
    return build_w1(llm, store).compile(checkpointer=checkpointer)

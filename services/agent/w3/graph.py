"""LangGraph wiring for the W3 scribe and authenticated doctor review interrupt."""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from services.agent.llm import JsonLLM
from services.agent.w3 import nodes as n
from services.agent.w3.nodes import W3State
from services.agent.w3.stock import StockSource
from services.agent.w3.store import W3Store
from services.common.tracing import Tracer, traced_node


def _review(s: W3State, store: W3Store) -> W3State:
    if n.review_expired(s, store):
        return {"action": "parked", "route": "parked"}
    response = interrupt({
        "type": "doctor_review",
        "draft_id": s["draft_id"],
        "draft": s["soap_json"],
        "flags": s.get("flags", []),
        "error": s.get("review_error"),
    })
    if not isinstance(response, dict) or response.get("action") not in {"approve", "edit", "reject"}:
        raise ValueError("doctor review requires action approve, edit, or reject")
    action = response["action"]
    if action == "approve" and any(flag.get("kind") == "grounding" for flag in s.get("flags", [])):
        return {"action": "blocked", "review_error": "Resolve grounding flags before signing."}
    if action == "edit":
        return {"action": action, "edits": response.get("edits", [])}
    return {"action": action, "review_error": None}


def _after_red_flags(s: W3State) -> str:
    return "park" if s.get("blocked_by_red_flag") else "model"


def _after_model(s: W3State) -> str:
    return "park" if s.get("model_unavailable") else "ground"


def _after_review(s: W3State) -> str:
    return {
        "approve": "sign",
        "edit": "edit",
        "reject": "reject",
        "blocked": "review",
        "parked": "end",
    }.get(s.get("action", ""), "end")


def build_w3(llm: JsonLLM, store: W3Store, stock: StockSource,
             tracer: Tracer | None = None) -> StateGraph:
    graph = StateGraph(W3State)
    functions = {
        "intake": partial(n.intake, store=store),
        "scrub": n.scrub_transcript,
        "red_flag_rules": n.red_flag_rules,
        "draft_soap": partial(n.draft_soap, llm=llm),
        "grounding_check": partial(n.grounding_check, stock=stock),
        "stock_check": partial(n.stock_check, stock=stock),
        "persist_draft": partial(n.persist_draft, store=store),
        "park_draft": partial(n.park_draft, store=store),
        "doctor_review": partial(_review, store=store),
        "apply_edits": partial(n.apply_edits, store=store),
        "reject_draft": partial(n.reject_draft, store=store),
        "sign_draft": partial(n.sign_draft, store=store),
        "notify": partial(n.notify_signed, store=store),
    }
    for name, function in functions.items():
        graph.add_node(name, traced_node(tracer, "w3", name, function))

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "scrub")
    graph.add_edge("scrub", "red_flag_rules")
    graph.add_conditional_edges("red_flag_rules", _after_red_flags,
                                {"park": "park_draft", "model": "draft_soap"})
    graph.add_conditional_edges("draft_soap", _after_model,
                                {"park": "park_draft", "ground": "grounding_check"})
    graph.add_edge("grounding_check", "stock_check")
    graph.add_edge("stock_check", "persist_draft")
    graph.add_edge("persist_draft", "doctor_review")
    graph.add_conditional_edges("doctor_review", _after_review, {
        "sign": "sign_draft", "edit": "apply_edits", "reject": "reject_draft",
        "review": "doctor_review", "end": END,
    })
    graph.add_edge("apply_edits", "grounding_check")
    graph.add_edge("reject_draft", END)
    graph.add_edge("park_draft", END)
    graph.add_edge("sign_draft", "notify")
    graph.add_edge("notify", END)
    return graph


def compile_w3(llm: JsonLLM, store: W3Store, stock: StockSource, checkpointer=None,
               tracer: Tracer | None = None):
    return build_w3(llm, store, stock, tracer).compile(checkpointer=checkpointer)

"""W2 ask graph: the LangGraph wiring (deck p.26 B). One request in, one reply out: no human pause.

scope_guard -> doctor_check -> retrieve -> grade -(weak)-> widen -> retrieve -> grade -(still weak)-> not_found
                                               \-(ok)-> answer -> verify -> END
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from services.agent.llm import JsonLLM
from services.agent.w2 import nodes as n
from services.agent.w2.nodes import RecordsTools, W2State
from services.common.tracing import Tracer, traced_node


def _after(s: W2State) -> str:
    return "stop" if s.get("route") not in (None, "continue") else "go"


def build_w2(llm: JsonLLM, tools: RecordsTools, tracer: Tracer | None = None) -> StateGraph:
    g = StateGraph(W2State)
    nodes = {
        "scope_guard": partial(n.scope_guard, tools=tools),
        "doctor_check": partial(n.doctor_check, tools=tools),
        "retrieve": partial(n.retrieve, tools=tools),
        "widen": n.widen,
        "not_found": n.not_found,
        "answer": partial(n.answer, llm=llm),
        "verify": n.verify,
    }
    for name, function in nodes.items():
        g.add_node(name, traced_node(tracer, "w2", name, function))

    g.add_edge(START, "scope_guard")
    g.add_conditional_edges("scope_guard", _after, {"stop": END, "go": "doctor_check"})
    g.add_conditional_edges("doctor_check", _after, {"stop": END, "go": "retrieve"})
    g.add_conditional_edges("retrieve", n.grade, {"answer": "answer", "widen": "widen", "not_found": "not_found"})
    g.add_edge("widen", "retrieve")
    g.add_edge("not_found", END)
    g.add_conditional_edges("answer", lambda s: "verify" if s.get("route") == "answer" else "end",
                            {"verify": "verify", "end": END})
    g.add_edge("verify", END)
    return g


def compile_w2(llm: JsonLLM, tools: RecordsTools, checkpointer=None, tracer: Tracer | None = None):
    return build_w2(llm, tools, tracer).compile(checkpointer=checkpointer)

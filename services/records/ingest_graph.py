"""Bounded per-document fan-out for queued PDF ingestion."""
from __future__ import annotations

import os
from typing import Annotated, Callable, TypedDict
from operator import add
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from services.common.tracing import Tracer


class IngestState(TypedDict, total=False):
    documents: list[dict]
    planned_documents: list[dict]
    results: Annotated[list[dict], add]
    joined_results: list[dict]
    trace_id: str
    parent_span_id: str


DocumentRunner = Callable[[dict, str, str | None], dict]


def _positive_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def ingest_max_concurrency() -> int:
    return _positive_setting("INGEST_MAX_CONCURRENCY", 4)


def build_ingest_graph(document_runner: DocumentRunner, tracer: Tracer | None = None):
    def plan(state: IngestState) -> dict:
        return {"planned_documents": list(state.get("documents", []))}

    def dispatch(state: IngestState):
        documents = state.get("planned_documents", [])
        if not documents:
            return "join"
        return [
            Send("ingest_document", {
                "document": document,
                "trace_id": state.get("trace_id", ""),
                "parent_span_id": state.get("parent_span_id"),
            })
            for document in documents
        ]

    def ingest_document(state: dict) -> dict:
        document = state["document"]
        doc_id = document["doc_id"]
        trace_id = state.get("trace_id", "")
        parent_span_id = state.get("parent_span_id")

        def run() -> dict:
            try:
                result = document_runner(document, trace_id, parent_span_id)
            except Exception as exc:
                result = {"doc_id": doc_id, "status": "failed", "error_type": type(exc).__name__}
            result.setdefault("doc_id", doc_id)
            if result.get("status") != "indexed":
                result.setdefault("status", "failed")
            return result

        if tracer is None:
            result = run()
        else:
            with tracer.span("node", "ingest.document", workflow="ingest", trace_id=trace_id,
                             parent_span_id=parent_span_id) as span:
                result = run()
                if result.get("status") != "indexed":
                    span.set_status("error", result.get("error_type", "IngestFailed"))
        return {"results": [result]}

    def join(state: IngestState) -> dict:
        order = {document["doc_id"]: index for index, document in enumerate(state.get("planned_documents", []))}
        return {"joined_results": sorted(
            state.get("results", []), key=lambda result: order.get(result["doc_id"], len(order)))}

    graph = StateGraph(IngestState)
    graph.add_node("plan", plan)
    graph.add_node("ingest_document", ingest_document)
    graph.add_node("join", join)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", dispatch, {"join": "join"})
    graph.add_edge("ingest_document", "join")
    graph.add_edge("join", END)
    return graph.compile()


def run_ingest_batch(graph, documents: list[dict], *, tracer: Tracer | None = None,
                     trace_id: str | None = None, max_concurrency: int | None = None) -> dict:
    trace_id = trace_id or uuid4().hex
    parent_context = (tracer.span("node", "ingest.batch", workflow="ingest", trace_id=trace_id)
                      if tracer else None)
    concurrency = ingest_max_concurrency() if max_concurrency is None else max_concurrency
    if concurrency < 1:
        raise ValueError("max_concurrency must be a positive integer")
    if parent_context is None:
        result = graph.invoke(
            {"documents": documents, "results": [], "trace_id": trace_id},
            config={"max_concurrency": concurrency},
        )
        result["results"] = result.pop("joined_results", result.get("results", []))
        return result
    with parent_context as parent:
        result = graph.invoke(
            {"documents": documents, "results": [], "trace_id": trace_id,
             "parent_span_id": parent.span.span_id},
            config={"max_concurrency": concurrency},
        )
        result["results"] = result.pop("joined_results", result.get("results", []))
        return result

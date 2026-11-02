"""In-region tracing with an exporter boundary and a deliberately narrow span schema."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from threading import RLock
import time
from typing import Literal, Protocol
from uuid import uuid4

SpanKind = Literal["node", "tool", "model", "http"]
SpanStatus = Literal["ok", "error", "timeout", "degraded"]

_LOGGER = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    workflow: str
    name: str
    kind: SpanKind
    started_at: datetime
    duration_ms: float = 0
    status: SpanStatus = "ok"
    model: str | None = None
    provider: str | None = None
    served_by: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error_type: str | None = None


class SpanExporter(Protocol):
    def export(self, span: TraceSpan) -> None: ...


class NullExporter:
    def export(self, _span: TraceSpan) -> None:
        return None


class InMemoryExporter:
    def __init__(self) -> None:
        self.spans: list[TraceSpan] = []

    def export(self, span: TraceSpan) -> None:
        self.spans.append(span)

    def read(self, trace_id: str | None = None, since: datetime | None = None) -> list[TraceSpan]:
        return [span for span in self.spans
                if (trace_id is None or span.trace_id == trace_id)
                and (since is None or span.started_at >= since)]


class PostgresExporter:
    def __init__(self, connection_pool: object) -> None:
        self.connection_pool = connection_pool

    def export(self, span: TraceSpan) -> None:
        query = """INSERT INTO trace_span
            (trace_id, span_id, parent_span_id, workflow, name, kind, started_at, duration_ms,
             status, model, provider, served_by, tokens_in, tokens_out, error_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        values = (span.trace_id, span.span_id, span.parent_span_id, span.workflow, span.name,
                  span.kind, span.started_at, span.duration_ms, span.status, span.model,
                  span.provider, span.served_by, span.tokens_in, span.tokens_out, span.error_type)
        if hasattr(self.connection_pool, "connection"):
            with self.connection_pool.connection() as conn:
                conn.execute(query, values)
        else:
            self.connection_pool.execute(query, values)

    def read(self, trace_id: str | None = None, since: datetime | None = None) -> list[dict]:
        clauses = []
        params: list[object] = []
        if trace_id is not None:
            clauses.append("trace_id = %s")
            params.append(trace_id)
        if since is not None:
            clauses.append("started_at >= %s")
            params.append(since)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = ("SELECT trace_id, span_id, parent_span_id, workflow, name, kind, started_at, duration_ms, "
                 "status, model, provider, served_by, tokens_in, tokens_out, error_type "
                 f"FROM trace_span{where} ORDER BY started_at, span_id")
        if hasattr(self.connection_pool, "connection"):
            with self.connection_pool.connection() as conn:
                return conn.execute(query, params).fetchall()
        return self.connection_pool.execute(query, params).fetchall()


@dataclass
class _ActiveSpan:
    span: TraceSpan
    started: float
    context_token: Token | None = None

    def set_status(self, status: SpanStatus, error_type: str | None = None) -> None:
        self.span.status = status
        self.span.error_type = error_type

    def update(self, *, model: str | None = None, provider: str | None = None,
               served_by: str | None = None, tokens_in: int | None = None,
               tokens_out: int | None = None) -> None:
        if model is not None:
            self.span.model = model
        if provider is not None:
            self.span.provider = provider
        if served_by is not None:
            self.span.served_by = served_by
        if tokens_in is not None:
            self.span.tokens_in = tokens_in
        if tokens_out is not None:
            self.span.tokens_out = tokens_out


class Tracer:
    def __init__(self, exporter: SpanExporter | None = None) -> None:
        self.exporter = exporter or NullExporter()
        self._active: ContextVar[_ActiveSpan | None] = ContextVar(
            f"sahyog_trace_span_{id(self)}", default=None)
        self._active_by_trace: dict[str, list[TraceSpan]] = {}

    def current_trace_id(self) -> str | None:
        active = self._active.get()
        return active.span.trace_id if active else None

    def set_trace_id(self, trace_id: str) -> None:
        active = self._active.get()
        if active is None:
            return
        old_trace_id = active.span.trace_id
        if old_trace_id != trace_id:
            with _ACTIVE_LOCK:
                active_span_list = self._active_by_trace.get(old_trace_id, [])
                if active.span in active_span_list:
                    active_span_list.remove(active.span)
                self._active_by_trace.setdefault(trace_id, []).append(active.span)
            active.span.trace_id = trace_id

    def span(self, kind: SpanKind, name: str, *, workflow: str = "", trace_id: str | None = None,
             model: str | None = None, provider: str | None = None, served_by: str | None = None,
             tokens_in: int | None = None, tokens_out: int | None = None) -> "_SpanContext":
        return _SpanContext(self, kind, name, workflow, trace_id, model, provider, served_by,
                            tokens_in, tokens_out)


_ACTIVE_LOCK = RLock()


class _SpanContext:
    def __init__(self, tracer: Tracer, kind: SpanKind, name: str, workflow: str,
                 trace_id: str | None, model: str | None, provider: str | None,
                 served_by: str | None, tokens_in: int | None, tokens_out: int | None) -> None:
        self.tracer = tracer
        self.kind = kind
        self.name = name
        self.workflow = workflow
        self.trace_id = trace_id
        self.model = model
        self.provider = provider
        self.served_by = served_by
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.active: _ActiveSpan | None = None

    def __enter__(self) -> _ActiveSpan:
        parent = self.tracer._active.get()
        trace_id = self.trace_id or (parent.span.trace_id if parent else None) or uuid4().hex
        parent_span_id = parent.span.span_id if parent and parent.span.trace_id == trace_id else None
        if parent_span_id is None:
            with _ACTIVE_LOCK:
                active = self.tracer._active_by_trace.get(trace_id, [])
                eligible = [span for span in active if span.kind in ("http", "node")]
                if eligible:
                    parent_span_id = eligible[-1].span_id
        span = TraceSpan(trace_id=trace_id, span_id=uuid4().hex, parent_span_id=parent_span_id,
                         workflow=self.workflow, name=self.name, kind=self.kind,
                         started_at=datetime.now(timezone.utc), model=self.model, provider=self.provider,
                         served_by=self.served_by, tokens_in=self.tokens_in, tokens_out=self.tokens_out)
        self.active = _ActiveSpan(span, time.perf_counter())
        self.active.context_token = self.tracer._active.set(self.active)
        with _ACTIVE_LOCK:
            self.tracer._active_by_trace.setdefault(trace_id, []).append(span)
        return self.active

    def __exit__(self, _exc_type: type[BaseException] | None, exc: BaseException | None,
                 _traceback: object) -> bool:
        assert self.active is not None
        span = self.active.span
        span.duration_ms = round((time.perf_counter() - self.active.started) * 1000, 3)
        if exc is not None and span.status == "ok":
            span.status = ("timeout" if isinstance(exc, TimeoutError)
                           or type(exc).__name__ == "ModelUnavailable" else "error")
            span.error_type = type(exc).__name__
        with _ACTIVE_LOCK:
            active = self.tracer._active_by_trace.get(span.trace_id, [])
            if span in active:
                active.remove(span)
            if not active:
                self.tracer._active_by_trace.pop(span.trace_id, None)
        if self.active.context_token is not None:
            self.tracer._active.reset(self.active.context_token)
        try:
            self.tracer.exporter.export(span)
        except Exception:
            _LOGGER.exception("Trace span export failed; request processing continues")
        return False


def traced_node(tracer: Tracer | None, workflow: str, name: str, function):
    if tracer is None:
        return function

    def wrapped(state, *args, **kwargs):
        trace_id = state.get("trace_id")
        with tracer.span("node", name, workflow=workflow, trace_id=trace_id) as span:
            try:
                result = function(state, *args, **kwargs)
            except BaseException as exc:
                if type(exc).__name__ == "GraphInterrupt":
                    span.set_status("ok")
                raise
            if result.get("degraded"):
                span.set_status("degraded")
            return result

    wrapped.__name__ = getattr(function, "__name__", name)
    wrapped.__doc__ = getattr(function, "__doc__", None)
    return wrapped


def trace_id_for(tracer: Tracer | None, explicit: str | None = None) -> str | None:
    return explicit or (tracer.current_trace_id() if tracer else None)

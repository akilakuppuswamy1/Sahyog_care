"""Tools for the W2 ask graph: consent check, filtered search, audit. Postgres and in-memory versions."""
from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from services.common.tracing import Tracer, trace_id_for


class PostgresRecordsTools:
    def __init__(self, conn, retriever, tracer: Tracer | None = None):
        self.conn, self.retriever = conn, retriever
        self.tracer = tracer

    def _trace(self, name: str, trace_id: str | None = None):
        return (self.tracer.span("tool", name, workflow="w2", trace_id=trace_id_for(self.tracer, trace_id))
                if self.tracer else nullcontext())

    def consent_valid(self, grantee_id: str, patient_id: str, scope: str) -> bool:
        with self._trace("consent_check"):
            return bool(self.conn.execute("SELECT consent_valid(%s, %s, %s)", (grantee_id, patient_id, scope)).fetchone()[0])

    def search(self, patient_id: str, question: str, k: int):
        with self._trace("record_search"):
            return self.retriever.search(patient_id, question, k=k)

    def audit(self, actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose, trace_id, detail=None):
        with self._trace("audit", trace_id):
            self.conn.execute(
                """INSERT INTO audit_event (actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose,
                                            trace_id, detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose, trace_id,
                 json.dumps(detail or {})))


@dataclass
class InMemoryRecordsTools:
    retriever: object
    consents: list[tuple[str, str, str, datetime]] = field(default_factory=list)   # (grantee, patient, scope, expires)
    events: list[dict] = field(default_factory=list)
    tracer: Tracer | None = field(default=None, repr=False)

    def _trace(self, name: str, trace_id: str | None = None):
        return (self.tracer.span("tool", name, workflow="w2", trace_id=trace_id_for(self.tracer, trace_id))
                if self.tracer else nullcontext())

    def consent_valid(self, grantee_id, patient_id, scope):
        with self._trace("consent_check"):
            now = datetime.now(timezone.utc)
            return any(g == grantee_id and p == patient_id and sc == scope and exp > now
                       for g, p, sc, exp in self.consents)

    def search(self, patient_id, question, k):
        with self._trace("record_search"):
            return self.retriever.search(patient_id, question, k=k)

    def audit(self, actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose, trace_id, detail=None):
        with self._trace("audit", trace_id):
            audit_detail = dict(detail or {})
            audit_detail.setdefault("trace_id", trace_id or trace_id_for(self.tracer))
            self.events.append(dict(actor_id=actor_id, actor_role=actor_role, action=action, resource_type=resource_type,
                                    resource_id=resource_id, patient_id=patient_id, purpose=purpose,
                                    trace_id=trace_id, detail=audit_detail))

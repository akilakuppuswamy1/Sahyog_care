"""Tools the graphs call, behind one interface: Postgres in the app, in-memory in unit tests.

Every function takes patient_id as an argument from graph state (set from the auth context).
No function accepts a patient_id typed by the model (deck p.36).
"""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from services.common.tracing import Tracer, trace_id_for

ACTOR = "agent:w1"


class SlotFull(RuntimeError):
    pass


class Store(Protocol):
    def shortlist(self, specialty_ids: list[str], language: str, n: int = 3) -> list[dict]: ...
    def book(self, patient_id: str, slot_id: str, idempotency_key: str) -> str: ...
    def escalate(self, kind: str, patient_id: str, trace_id: str, detail: dict) -> None: ...
    def audit(self, action: str, resource_type: str, resource_id: str, patient_id: str | None,
              purpose: str, trace_id: str, detail: dict | None = None) -> None: ...


def _lang(language: str) -> str:
    return language.split("-")[0] if language and language != "und" else "en"


class PostgresStore:
    def __init__(self, conn, tracer: Tracer | None = None):          # a psycopg Connection with autocommit=True
        self.conn = conn
        self.tracer = tracer

    def _trace(self, name: str, trace_id: str | None = None):
        return (self.tracer.span("tool", name, workflow="w1", trace_id=trace_id_for(self.tracer, trace_id))
                if self.tracer else nullcontext())

    def shortlist(self, specialty_ids: list[str], language: str, n: int = 3) -> list[dict]:
        with self._trace("shortlist"):
            out: list[dict] = []
            for sp in specialty_ids:
                if len(out) >= n:
                    break
                rows = self.conn.execute(
                    """
                    SELECT d.doctor_id, d.name, d.languages, d.specialty_id, sp.name, c.clinic_id, c.name, c.city,
                           s.slot_id, s.starts_at
                      FROM doctor d
                      JOIN clinic c USING (clinic_id)
                      JOIN specialty sp USING (specialty_id)
                      JOIN LATERAL (SELECT slot_id, starts_at FROM slot s
                                     WHERE s.doctor_id = d.doctor_id AND s.starts_at > now() AND s.booked < s.capacity
                                     ORDER BY s.starts_at LIMIT 1) s ON true
                     WHERE d.specialty_id = %s
                     ORDER BY (%s = ANY (d.languages)) DESC, s.starts_at, d.doctor_id
                     LIMIT %s
                    """, (sp, _lang(language), n - len(out))).fetchall()
                out += [dict(doctor_id=r[0], doctor_name=r[1], languages=list(r[2]), specialty_id=r[3], specialty=r[4],
                             clinic_id=r[5], clinic=r[6], city=r[7], slot_id=r[8], starts_at=r[9].isoformat())
                        for r in rows]
            return out

    def book(self, patient_id: str, slot_id: str, idempotency_key: str) -> str:
        with self._trace("book"):
            import psycopg
            try:
                appt = self.conn.execute("SELECT book_slot(%s, %s, %s, 'assistant')",
                                         (patient_id, slot_id, idempotency_key)).fetchone()[0]
            except psycopg.errors.RaiseException as e:
                raise SlotFull(str(e)) from e
            return str(appt)

    def escalate(self, kind: str, patient_id: str, trace_id: str, detail: dict) -> None:
        with self._trace("escalate", trace_id):
            self.audit("escalate", "w1_triage", trace_id, patient_id, kind, trace_id, detail)

    def audit(self, action, resource_type, resource_id, patient_id, purpose, trace_id, detail=None) -> None:
        with self._trace("audit", trace_id):
            self.conn.execute(
                """INSERT INTO audit_event (actor_id, actor_role, action, resource_type, resource_id,
                                            patient_id, purpose, trace_id, detail)
                   VALUES (%s, 'system', %s, %s, %s, %s, %s, %s, %s)""",
                (ACTOR, action, resource_type, resource_id, patient_id, purpose, trace_id, json.dumps(detail or {})))

    def ensure_demo_patient(self, patient_id: str, name: str = "Demo Patient", dob: str = "1992-01-01") -> None:
        self.conn.execute("""INSERT INTO patient (patient_id, name, dob, language, synthetic)
                             VALUES (%s, %s, %s, 'en', true) ON CONFLICT (patient_id) DO NOTHING""",
                          (patient_id, name, dob))


@dataclass
class InMemoryStore:
    """Same behaviour as the SQL functions, for unit tests: capacity 1, idempotent booking, audit list."""
    doctors: list[dict] = field(default_factory=list)
    booked: dict[str, str] = field(default_factory=dict)            # slot_id -> appt_id
    keys: dict[tuple[str, str], str] = field(default_factory=dict)  # (patient, key) -> appt_id
    events: list[dict] = field(default_factory=list)
    tracer: Tracer | None = field(default=None, repr=False)

    def _trace(self, name: str, trace_id: str | None = None):
        return (self.tracer.span("tool", name, workflow="w1", trace_id=trace_id_for(self.tracer, trace_id))
                if self.tracer else nullcontext())

    @classmethod
    def demo(cls) -> "InMemoryStore":
        t0 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        docs = []
        for i, (sp, lang) in enumerate([("SP07", ["en", "hi", "mr"]), ("SP07", ["en", "te"]), ("SP07", ["en"]),
                                         ("SP08", ["en", "hi"]), ("SP20", ["en", "hi"]), ("SP18", ["en", "hi"]),
                                         ("SP18", ["en"]), ("SP03", ["en", "hi"])], start=1):
            docs.append(dict(doctor_id=f"DR{i:04d}", doctor_name=f"Dr. Test {i}", languages=lang, specialty_id=sp,
                             specialty=sp, clinic_id="PUN-H01", clinic="Sahyog Hospital Baner", city="Pune",
                             slot_id=f"DR{i:04d}-20261116-{9 + i:02d}00", starts_at=(t0 + timedelta(hours=i)).isoformat()))
        return cls(doctors=docs)

    def shortlist(self, specialty_ids, language, n=3):
        with self._trace("shortlist"):
            lang = _lang(language)
            out: list[dict] = []
            for sp in specialty_ids:
                pool = [d for d in self.doctors if d["specialty_id"] == sp and d["slot_id"] not in self.booked]
                pool.sort(key=lambda d: (lang not in d["languages"], d["starts_at"], d["doctor_id"]))
                out += pool[: n - len(out)]
                if len(out) >= n:
                    break
            return out

    def book(self, patient_id, slot_id, idempotency_key):
        with self._trace("book"):
            if (patient_id, idempotency_key) in self.keys:
                return self.keys[(patient_id, idempotency_key)]
            if slot_id in self.booked or slot_id not in {d["slot_id"] for d in self.doctors}:
                raise SlotFull(f"slot {slot_id} is full or does not exist")
            appt = str(uuid.uuid4())
            if not hasattr(self, "appointments"): self.appointments = {}
            self.appointments[appt] = dict(appt_id=appt, patient_id=patient_id, slot_id=slot_id, status="booked", booked_by="assistant")
            self.booked[slot_id] = appt
            self.keys[(patient_id, idempotency_key)] = appt
            return appt

    def escalate(self, kind, patient_id, trace_id, detail):
        with self._trace("escalate", trace_id):
            self.audit("escalate", "w1_triage", trace_id, patient_id, kind, trace_id, detail)

    def audit(self, action, resource_type, resource_id, patient_id, purpose, trace_id, detail=None):
        with self._trace("audit", trace_id):
            audit_detail = dict(detail or {})
            audit_detail.setdefault("trace_id", trace_id or trace_id_for(self.tracer))
            self.events.append(dict(action=action, resource_type=resource_type, resource_id=resource_id,
                                    patient_id=patient_id, purpose=purpose, trace_id=trace_id,
                                    detail=audit_detail))

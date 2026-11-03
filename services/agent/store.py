"""Tools the graphs call, behind one interface: Postgres in the app, in-memory in unit tests.

Every function takes patient_id as an argument from graph state (set from the auth context).
No function accepts a patient_id typed by the model (deck p.36).
"""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo
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

    def list_specialties(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT specialty_id, name FROM specialty ORDER BY name, specialty_id"
        ).fetchall()
        return [dict(specialty_id=row[0], name=row[1]) for row in rows]

    def find_doctors(self, specialty_id: str, language: str | None = None,
                     clinic_id: str | None = None, limit: int = 10) -> list[dict]:
        language = _lang(language) if language else None
        rows = self.conn.execute(
            """SELECT d.doctor_id, d.name, c.clinic_id, c.name, d.languages
                 FROM doctor d JOIN clinic c USING (clinic_id)
                WHERE d.specialty_id = %s
                  AND (%s::text IS NULL OR %s = ANY (d.languages))
                  AND (%s::text IS NULL OR d.clinic_id = %s)
                ORDER BY d.name, d.doctor_id LIMIT %s""",
            (specialty_id, language, language, clinic_id, clinic_id, limit),
        ).fetchall()
        return [dict(doctor_id=row[0], name=row[1], clinic_id=row[2], clinic=row[3],
                     languages=list(row[4])) for row in rows]

    def next_slots(self, specialty_id: str | None = None, doctor_id: str | None = None,
                   from_date: date | None = None, limit: int = 10) -> list[dict]:
        if (specialty_id is None) == (doctor_id is None):
            raise ValueError("Provide exactly one of specialty_id or doctor_id")
        start = (datetime.combine(from_date, datetime.min.time(),
                                  tzinfo=ZoneInfo("Asia/Kolkata"))
                 if from_date else datetime.now(timezone.utc))
        rows = self.conn.execute(
            """SELECT s.slot_id, s.starts_at
                 FROM slot s JOIN doctor d USING (doctor_id)
                WHERE s.starts_at >= %s AND s.booked < s.capacity
                  AND (%s::text IS NULL OR d.specialty_id = %s)
                  AND (%s::text IS NULL OR d.doctor_id = %s)
                ORDER BY s.starts_at, s.slot_id LIMIT %s""",
            (start, specialty_id, specialty_id, doctor_id, doctor_id, limit),
        ).fetchall()
        return [dict(slot_id=row[0], starts_at=row[1]) for row in rows]

    def get_booking(self, appt_id: str, patient_id: str, staff_access: bool = False) -> dict | None:
        try:
            appt_id = uuid.UUID(appt_id)
        except (ValueError, TypeError, AttributeError):
            return None
        row = self.conn.execute(
            """SELECT appt_id, patient_id, status FROM appointment
                WHERE appt_id = %s AND (%s OR patient_id = %s)""",
            (appt_id, staff_access, patient_id),
        ).fetchone()
        return (dict(appt_id=str(row[0]), patient_id=row[1], status=row[2]) if row else None)

    def book(self, patient_id: str, slot_id: str, idempotency_key: str,
             booked_by: str = "assistant", trace: bool = True) -> str:
        with (self._trace("book") if trace else nullcontext()):
            import psycopg
            try:
                appt = self.conn.execute("SELECT book_slot(%s, %s, %s, %s)",
                                         (patient_id, slot_id, idempotency_key, booked_by)).fetchone()[0]
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

    def list_specialties(self):
        names = {doctor["specialty_id"]: doctor["specialty"] for doctor in self.doctors}
        return [dict(specialty_id=specialty_id, name=name)
                for specialty_id, name in sorted(names.items(), key=lambda item: (item[1], item[0]))]

    def find_doctors(self, specialty_id, language=None, clinic_id=None, limit=10):
        lang = _lang(language) if language else None
        doctors = [
            doctor for doctor in self.doctors
            if doctor["specialty_id"] == specialty_id
            and (lang is None or lang in doctor["languages"])
            and (clinic_id is None or clinic_id == doctor["clinic_id"])
        ]
        doctors.sort(key=lambda doctor: (doctor["doctor_name"], doctor["doctor_id"]))
        return [
            dict(doctor_id=doctor["doctor_id"], name=doctor["doctor_name"],
                 clinic_id=doctor["clinic_id"], clinic=doctor["clinic"],
                 languages=list(doctor["languages"]))
            for doctor in doctors[:limit]
        ]

    def next_slots(self, specialty_id=None, doctor_id=None, from_date=None, limit=10):
        if (specialty_id is None) == (doctor_id is None):
            raise ValueError("Provide exactly one of specialty_id or doctor_id")
        start = (datetime.combine(from_date, datetime.min.time(),
                                  tzinfo=ZoneInfo("Asia/Kolkata"))
                 if from_date else datetime.now(timezone.utc))
        slots = [
            dict(slot_id=doctor["slot_id"], starts_at=doctor["starts_at"])
            for doctor in self.doctors
            if doctor["slot_id"] not in self.booked
            and (specialty_id is None or doctor["specialty_id"] == specialty_id)
            and (doctor_id is None or doctor["doctor_id"] == doctor_id)
            and datetime.fromisoformat(doctor["starts_at"].replace("Z", "+00:00")) >= start
        ]
        slots.sort(key=lambda slot: (slot["starts_at"], slot["slot_id"]))
        return slots[:limit]

    def get_booking(self, appt_id, patient_id, staff_access=False):
        appointment = getattr(self, "appointments", {}).get(appt_id)
        if not appointment or (not staff_access and appointment["patient_id"] != patient_id):
            return None
        return dict(appt_id=appt_id, patient_id=appointment["patient_id"],
                    status=appointment["status"])

    def book(self, patient_id, slot_id, idempotency_key, booked_by="assistant", trace=True):
        with (self._trace("book") if trace else nullcontext()):
            if (patient_id, idempotency_key) in self.keys:
                return self.keys[(patient_id, idempotency_key)]
            if slot_id in self.booked or slot_id not in {d["slot_id"] for d in self.doctors}:
                raise SlotFull(f"slot {slot_id} is full or does not exist")
            appt = str(uuid.uuid4())
            if not hasattr(self, "appointments"): self.appointments = {}
            self.appointments[appt] = dict(appt_id=appt, patient_id=patient_id, slot_id=slot_id,
                                           status="booked", booked_by=booked_by)
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

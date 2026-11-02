"""Persistence boundary for W3 drafts, immutable notes, and signing audit events."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol


class W3Store(Protocol):
    def get_encounter(self, encounter_id: str, doctor_id: str) -> dict: ...
    def next_version(self, encounter_id: str) -> int: ...
    def save_draft(self, draft: dict, status: str = "draft") -> datetime: ...
    def record_edit(self, draft_id: str, field: str, before: object, after: object, doctor_id: str) -> None: ...
    def set_draft_status(self, draft_id: str, status: str) -> None: ...
    def sign_draft(self, draft: dict, doctor_id: str, trace_id: str) -> dict: ...
    def park_expired_drafts(self, timeout_min: float) -> list[str]: ...
    def notify_signed(self, note: dict, patient_id: str, trace_id: str) -> dict: ...


def _edit_distance(before: object, after: object) -> int:
    left = json.dumps(before, ensure_ascii=False, sort_keys=True)
    right = json.dumps(after, ensure_ascii=False, sort_keys=True)
    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        for col_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[col_index] + 1,
                previous[col_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def edit_distances(before: dict, after: dict) -> dict[str, int]:
    distances = {}
    for section in ("S", "O", "A", "P"):
        if before.get(section) != after.get(section):
            distances[section] = _edit_distance(before.get(section), after.get(section))
    before_medicines = [
        {key: item.get(key, "") for key in ("name", "molecule", "dose", "frequency", "quote")}
        for item in before.get("medicines", [])
    ]
    after_medicines = [
        {key: item.get(key, "") for key in ("name", "molecule", "dose", "frequency", "quote")}
        for item in after.get("medicines", [])
    ]
    if before_medicines != after_medicines:
        distances["medicines"] = _edit_distance(before_medicines, after_medicines)
    return distances


class PostgresW3Store:
    def __init__(self, conn):
        self.conn = conn

    def get_encounter(self, encounter_id: str, doctor_id: str) -> dict:
        row = self.conn.execute(
            """SELECT e.encounter_id, e.patient_id, e.doctor_id, e.clinic_id, d.clinic_id
                 FROM encounter e JOIN doctor d ON d.doctor_id = e.doctor_id
                WHERE e.encounter_id = %s""",
            (encounter_id,),
        ).fetchone()
        if row is None:
            raise LookupError("encounter not found")
        if row[2] != doctor_id:
            raise PermissionError("only the treating doctor may access this encounter")
        clinic_id = row[3] or row[4]
        if not clinic_id or clinic_id != row[4]:
            raise ValueError("encounter clinic does not match the treating doctor's clinic")
        return {"encounter_id": row[0], "patient_id": row[1], "doctor_id": row[2], "clinic_id": clinic_id}

    def next_version(self, encounter_id: str) -> int:
        active = self.conn.execute(
            "SELECT 1 FROM note_draft WHERE encounter_id = %s AND status = 'draft' LIMIT 1",
            (encounter_id,),
        ).fetchone()
        if active:
            raise ValueError("this encounter already has an active doctor review")
        row = self.conn.execute(
            "SELECT COALESCE(max(version), 0) + 1 FROM clinical_note WHERE encounter_id = %s",
            (encounter_id,),
        ).fetchone()
        return int(row[0])

    def save_draft(self, draft: dict, status: str = "draft") -> datetime:
        row = self.conn.execute(
            """INSERT INTO note_draft
                   (draft_id, encounter_id, doctor_id, version, soap_json, flags, status, review_started_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                       CASE WHEN %s = 'draft' THEN now() END)
               ON CONFLICT (draft_id) DO UPDATE
                   SET soap_json = EXCLUDED.soap_json, flags = EXCLUDED.flags, status = EXCLUDED.status
                 WHERE note_draft.status IN ('draft','parked')
               RETURNING review_started_at""",
            (draft["draft_id"], draft["encounter_id"], draft["doctor_id"], draft["version"],
             json.dumps(draft["soap_json"]), json.dumps(draft.get("flags", [])), status, status),
        ).fetchone()
        if row is None:
            raise ValueError("draft is already signed or rejected")
        return row[0]

    def record_edit(self, draft_id: str, field: str, before: object, after: object, doctor_id: str) -> None:
        self.conn.execute(
            """INSERT INTO note_edit (draft_id, field, "before", "after", edited_by)
               VALUES (%s, %s, %s::jsonb, %s::jsonb, %s)""",
            (draft_id, field, json.dumps(before), json.dumps(after), doctor_id),
        )

    def set_draft_status(self, draft_id: str, status: str) -> None:
        row = self.conn.execute(
            """UPDATE note_draft SET status = %s
                WHERE draft_id = %s AND status IN ('draft','parked')
                RETURNING draft_id""",
            (status, draft_id),
        ).fetchone()
        if row is None:
            raise ValueError("draft is no longer editable")

    def sign_draft(self, draft: dict, doctor_id: str, trace_id: str) -> dict:
        with self.conn.transaction():
            encounter = self.conn.execute(
                """SELECT patient_id, doctor_id FROM encounter
                    WHERE encounter_id = %s FOR UPDATE""",
                (draft["encounter_id"],),
            ).fetchone()
            if encounter is None:
                raise LookupError("encounter not found")
            if encounter[1] != doctor_id or doctor_id != draft["doctor_id"]:
                raise PermissionError("only the treating doctor may sign this note")
            status = self.conn.execute(
                """SELECT status FROM note_draft
                    WHERE draft_id = %s AND encounter_id = %s AND doctor_id = %s
                    FOR UPDATE""",
                (draft["draft_id"], draft["encounter_id"], doctor_id),
            ).fetchone()
            if status is None or status[0] != "draft":
                raise ValueError("only an active draft can be signed")
            prior = self.conn.execute(
                """SELECT note_id, version FROM clinical_note
                    WHERE encounter_id = %s ORDER BY version DESC LIMIT 1""",
                (draft["encounter_id"],),
            ).fetchone()
            version = int(prior[1]) + 1 if prior else 1
            if draft["version"] != version:
                raise ValueError("draft version is stale; start a fresh doctor review")
            supersedes = prior[0] if prior else None
            distances = edit_distances(draft["initial_soap_json"], draft["soap_json"])
            note = self.conn.execute(
                """INSERT INTO clinical_note
                       (encounter_id, doctor_id, version, soap_json, edit_history,
                        signed_by, signed_at, content_hash, supersedes)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, now(), 'pending', %s)
                   RETURNING note_id, version, signed_at, content_hash, supersedes""",
                (draft["encounter_id"], doctor_id, version, json.dumps(draft["soap_json"]),
                 json.dumps(draft.get("edit_history", [])), doctor_id, supersedes),
            ).fetchone()
            note_id = str(note[0])
            for medicine in draft.get("medicines", []):
                if medicine.get("molecule") == "[doctor to specify]":
                    continue
                self.conn.execute(
                    """INSERT INTO prescription (note_id, medicine_id, medicine_text, dose, substitute_ok)
                       VALUES (%s, %s, %s, %s, false)""",
                    (note[0], medicine.get("medicine_id"), medicine.get("name") or medicine.get("molecule"),
                     medicine.get("dose")),
                )
            self.conn.execute(
                """UPDATE note_draft SET status = 'signed', signed_note_id = %s
                    WHERE draft_id = %s""",
                (note[0], draft["draft_id"]),
            )
            review_started = self.conn.execute(
                "SELECT review_started_at FROM note_draft WHERE draft_id = %s", (draft["draft_id"],)
            ).fetchone()[0]
            review_minutes = ((datetime.now(timezone.utc) - review_started).total_seconds() / 60
                              if review_started else None)
            self.conn.execute(
                """INSERT INTO audit_event
                       (actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose,
                        trace_id, detail)
                   VALUES (%s, 'doctor', 'sign', 'clinical_note', %s, %s, 'treatment', %s, %s::jsonb)""",
                (doctor_id, note_id, encounter[0], trace_id,
                 json.dumps({"draft_id": draft["draft_id"], "version": version,
                             "stock_flags": [flag for flag in draft.get("flags", [])
                                             if flag.get("kind") == "stock"],
                             "edit_distance_by_field": distances,
                             "review_minutes": review_minutes})),
            )
        return {"note_id": note_id, "version": int(note[1]), "signed_at": note[2],
                "content_hash": note[3], "supersedes": str(note[4]) if note[4] else None,
                "edit_distance_by_field": distances}

    def park_expired_drafts(self, timeout_min: float) -> list[str]:
        rows = self.conn.execute(
            """UPDATE note_draft SET status = 'parked'
                WHERE status = 'draft'
                  AND review_started_at <= now() - make_interval(secs => %s)
                RETURNING draft_id""",
            (timeout_min * 60,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def notify_signed(self, note: dict, patient_id: str, trace_id: str) -> dict:
        self.conn.execute(
            """INSERT INTO audit_event
                   (actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose,
                    trace_id, detail)
               VALUES ('agent:w3', 'system', 'write', 'notification', %s, %s,
                       'clinical_note_signed', %s, %s::jsonb)""",
            (note["note_id"], patient_id, trace_id,
             json.dumps({"template": "clinical_note_signed_v1", "version": note["version"]})),
        )
        return {"type": "clinical_note_signed", "note_id": note["note_id"], "version": note["version"]}


@dataclass
class InMemoryW3Store:
    """Test store with the same authorization, version, and immutable-signing behavior."""
    encounters: dict[str, dict]
    notes: list[dict] = field(default_factory=list)
    drafts: dict[str, dict] = field(default_factory=dict)
    edits: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def get_encounter(self, encounter_id: str, doctor_id: str) -> dict:
        encounter = self.encounters.get(encounter_id)
        if encounter is None:
            raise LookupError("encounter not found")
        if encounter["doctor_id"] != doctor_id:
            raise PermissionError("only the treating doctor may access this encounter")
        return dict(encounter)

    def next_version(self, encounter_id: str) -> int:
        versions = [note["version"] for note in self.notes if note["encounter_id"] == encounter_id]
        if any(draft["encounter_id"] == encounter_id and draft["status"] == "draft"
               for draft in self.drafts.values()):
            raise ValueError("this encounter already has an active doctor review")
        return max(versions, default=0) + 1

    def save_draft(self, draft: dict, status: str = "draft") -> datetime:
        previous = self.drafts.get(draft["draft_id"], {})
        if previous.get("status") in ("signed", "rejected"):
            raise ValueError("draft is already signed or rejected")
        if status == "draft" and any(
            draft_id != draft["draft_id"] and other["encounter_id"] == draft["encounter_id"]
            and other["version"] == draft["version"] and other["status"] == "draft"
            for draft_id, other in self.drafts.items()
        ):
            raise ValueError("this encounter already has an active doctor review")
        started = previous.get("review_started_at") or (
            datetime.now(timezone.utc) if status == "draft" else None
        )
        self.drafts[draft["draft_id"]] = {**draft, "status": status, "review_started_at": started}
        return started

    def record_edit(self, draft_id: str, field: str, before: object, after: object, doctor_id: str) -> None:
        self.edits.append({"draft_id": draft_id, "field": field, "before": before, "after": after,
                           "edited_by": doctor_id})

    def set_draft_status(self, draft_id: str, status: str) -> None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["status"] not in ("draft", "parked"):
            raise ValueError("draft is no longer editable")
        draft["status"] = status

    def sign_draft(self, draft: dict, doctor_id: str, trace_id: str) -> dict:
        saved = self.drafts.get(draft["draft_id"])
        if saved is None or saved["status"] != "draft":
            raise ValueError("only an active draft can be signed")
        if saved["doctor_id"] != doctor_id or self.encounters[saved["encounter_id"]]["doctor_id"] != doctor_id:
            raise PermissionError("only the treating doctor may sign this note")
        prior = max((note for note in self.notes if note["encounter_id"] == saved["encounter_id"]),
                    key=lambda note: note["version"], default=None)
        version = prior["version"] + 1 if prior else 1
        if saved["version"] != version:
            raise ValueError("draft version is stale; start a fresh doctor review")
        signed = {
            "note_id": str(uuid.uuid4()), "encounter_id": saved["encounter_id"], "doctor_id": doctor_id,
            "version": version, "soap_json": saved["soap_json"],
            "signed_by": doctor_id, "content_hash": hashlib.sha256(
                json.dumps(saved["soap_json"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            "supersedes": prior["note_id"] if prior else None,
            "edit_distance_by_field": edit_distances(saved["initial_soap_json"], saved["soap_json"]),
        }
        self.notes.append(signed)
        saved["status"] = "signed"
        saved["signed_note_id"] = signed["note_id"]
        self.events.append({"action": "sign", "doctor_id": doctor_id, "note_id": signed["note_id"],
                            "trace_id": trace_id})
        return signed

    def park_expired_drafts(self, timeout_min: float) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_min)
        parked = []
        for draft_id, draft in self.drafts.items():
            started = draft.get("review_started_at")
            if draft["status"] == "draft" and started is not None and started <= cutoff:
                draft["status"] = "parked"
                parked.append(draft_id)
        return parked

    def notify_signed(self, note: dict, patient_id: str, trace_id: str) -> dict:
        event = {"type": "clinical_note_signed", "note_id": note["note_id"], "version": note["version"]}
        self.events.append({"action": "write", "resource_type": "notification", "patient_id": patient_id,
                            "trace_id": trace_id, **event})
        return event

"""W3 scribe node logic. No node sends transcript text to tracing or persistence metadata."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from services.agent.llm import JsonLLM, ModelError
from services.agent.w1.guards import scrub
from services.agent.w1.redflags import evaluate
from services.agent.w3.formulary import PLACEHOLDER, Formulary, default_formulary, split_name
from services.agent.w3.stock import StockSource
from services.agent.w3.store import W3Store


PROMPT_VERSION = "w3-prompts-v2"   # v2: S/O/A/P roles, P never empty, quote holds name + final dose (run 20261102-220544)
SOAP_FIELDS = ("S", "O", "A", "P")
GROUNDING_KINDS = frozenset({"grounding", "molecule_unresolved", "molecule_ambiguous"})
MEDICINE_CLASSES = re.compile(
    r"\b(statins?|antibiotics?|beta[ -]?blockers?|ace inhibitors?|nsaids?|"
    r"anticoagulants?|blood thinners?|diuretics?|steroids?)\b",
    re.I,
)
DOSE_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mcg|μg|mg|g|ml|iu|units?|tablets?|capsules?)\b", re.I
)
CORRECTION_PATTERN = re.compile(r"\b(?:sorry|actually|make that|rather|correction|i mean)\b", re.I)


class W3State(TypedDict, total=False):
    transcript: str
    encounter_id: str
    doctor_id: str
    trace_id: str
    draft_id: str
    version: int
    patient_id: str
    clinic_id: str
    soap_json: dict
    initial_soap_json: dict
    medicines: list[dict]
    flags: list[dict]
    edit_history: list[dict]
    edits: list[dict]
    review_started_at: datetime
    action: str
    review_error: str
    note: dict
    notification: dict
    model_unavailable: bool
    blocked_by_red_flag: bool
    route: str


def intake(s: W3State, store: W3Store) -> W3State:
    transcript = s.get("transcript", "")
    encounter_id, doctor_id = s.get("encounter_id", ""), s.get("doctor_id", "")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("transcript is required")
    if not encounter_id or not doctor_id:
        raise PermissionError("encounter and authenticated doctor are required")
    encounter = store.get_encounter(encounter_id, doctor_id)
    return {
        "transcript": transcript.strip(),
        "encounter_id": encounter_id,
        "doctor_id": doctor_id,
        "patient_id": encounter["patient_id"],
        "clinic_id": encounter["clinic_id"],
        "draft_id": s.get("draft_id") or str(uuid.uuid4()),
        "version": store.next_version(encounter_id),
        "trace_id": s.get("trace_id") or uuid.uuid4().hex,
        "flags": [],
        "edit_history": [],
        "action": "",
    }


def scrub_transcript(s: W3State) -> W3State:
    cleaned, _token_map = scrub(s["transcript"])
    return {"transcript": cleaned}


def red_flag_rules(s: W3State) -> W3State:
    result = evaluate(s["transcript"])
    if not result.fired:
        return {"blocked_by_red_flag": False}
    flags = [{"kind": "red_flag", "message": item} for item in result.fired]
    return {"blocked_by_red_flag": True, "flags": flags, "route": "manual_review"}


SYSTEM_PROMPT = """You draft, but never sign, an Indian clinical SOAP note from a doctor-patient dictation.
The dictation may mix English, Hindi and Hinglish; write the note in English.
S (subjective): what the patient reports: symptoms, history, complaints, how they take their medicines.
O (objective): what the doctor observes or measures: examination findings, vitals, results read out.
A (assessment): the doctor's assessment exactly as stated. Never add, upgrade or guess a diagnosis.
P (plan): every instruction the doctor gives: medicines, tests, advice, referrals and follow-up.
P must not be empty if the doctor gave any instruction. Use "" for a section with no content.
Do not add facts, diagnoses, medicines, molecules, doses, or frequencies not stated in the transcript.
Keep S, O, A, and P concise and faithful; do not repeat one sentence in several sections.
Medicines: list every medicine the doctor names, as spoken (brand, generic, or class).
- name: the medicine as spoken. Use a brand only as the name.
- molecule: only a molecule the doctor actually says, otherwise "". Never infer it from a brand or a class.
- dose: the final dose after any correction ("sorry", "make that"), as spoken.
- frequency: as spoken, otherwise "".
- quote: one exact contiguous span of the transcript that contains the medicine name AND the final
  (corrected) dose, and the frequency when stated.
If only a class is named (statin, antibiotic), name is the class, and molecule and dose are "".
Return JSON only:
{"S":"...","O":"...","A":"...","P":"...","medicines":[{"name":"...","molecule":"...",
"dose":"...","frequency":"...","quote":"exact transcript words"}]}"""


def draft_soap(s: W3State, llm: JsonLLM) -> W3State:
    system = SYSTEM_PROMPT
    try:
        result = llm.complete_json(
            system, s["transcript"], purpose="w3.draft_soap", trace_id=s["trace_id"]
        )
        if not isinstance(result, dict):
            raise ModelError("W3 model response must be a JSON object")
        soap = {field: result.get(field, "") for field in SOAP_FIELDS}
        if any(not isinstance(value, str) for value in soap.values()):
            raise ModelError("W3 SOAP fields must be strings")
        medicines = result.get("medicines", [])
        if not isinstance(medicines, list) or any(not isinstance(item, dict) for item in medicines):
            raise ModelError("W3 medicines must be a JSON array of objects")
        normalized = []
        for item in medicines:
            row = {}
            for key in ("name", "molecule", "dose", "frequency", "quote"):
                value = item.get(key, "")
                if not isinstance(value, str):
                    raise ModelError(f"W3 medicine field {key} must be a string")
                row[key] = value.strip()
            normalized.append(row)
    except (ModelError, TimeoutError):
        soap = {field: "" for field in SOAP_FIELDS}
        normalized = []
        return {
            "soap_json": {**soap, "medicines": normalized},
            "initial_soap_json": {**soap, "medicines": normalized},
            "medicines": normalized,
            "flags": [{"kind": "model_unavailable", "message": "model unavailable"}],
            "model_unavailable": True,
            "action": "park",
        }
    soap_json = {**soap, "medicines": normalized}
    return {
        "soap_json": soap_json,
        "initial_soap_json": json.loads(json.dumps(soap_json)),
        "medicines": normalized,
        "flags": [],
        "model_unavailable": False,
    }


def _key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _contains(text: str, phrase: str) -> bool:
    key = _key(phrase)
    return bool(key) and f" {key} " in f" {_key(text)} "


def _class_in_quote(text: str) -> str | None:
    match = MEDICINE_CLASSES.search(text)
    return match.group(0) if match else None


def _dose_is_current(quote: str, dose: str) -> bool:
    if not _contains(quote, dose):
        return False
    mentions = list(DOSE_PATTERN.finditer(quote))
    correction = list(CORRECTION_PATTERN.finditer(quote))
    if not mentions or not correction:
        return True
    last_correction = correction[-1].start()
    later_doses = [mention for mention in mentions if mention.start() > last_correction]
    if not later_doses:
        return True
    dose_amount = DOSE_PATTERN.search(dose)
    return bool(dose_amount and _key(dose_amount.group(0)) == _key(later_doses[-1].group(0)))


def _flag(kind: str, index: int, field: str, message: str, **extra) -> dict:
    return {"kind": kind, "field": f"medicines.{index}.{field}", "message": message, **extra}


def _name_in(text: str, name: str) -> bool:
    """The name, or its brand words without form/strength ('Atorva Tablet' -> 'atorva'), is in text."""
    words = " ".join(split_name(name).words)
    return bool(name) and (_contains(text, name) or bool(words) and _contains(text, words))


def _ground_medicine(medicine: dict, index: int, transcript: str, formulary: Formulary,
                     flags: list[dict]) -> dict:
    """Molecule rule: stated in the quote, or the stated brand maps to one formulary composition."""
    quote, name, molecule = medicine.get("quote", ""), medicine.get("name", ""), medicine.get("molecule", "")
    quote_supported = bool(quote) and _contains(transcript, quote)
    medicine["source"] = None
    if not quote_supported:
        stated = next((cls for cls in (_class_in_quote(name), _class_in_quote(quote))
                       if cls and _contains(transcript, cls)), None)
        medicine.update(name=stated or PLACEHOLDER, molecule=PLACEHOLDER, quote="")
        flags.append(_flag("grounding", index, "quote", "medicine quote is not present in the transcript"))
        flags.append(_flag("molecule_unresolved", index, "molecule",
                           "molecule is not stated; doctor to specify"))
    else:
        name_supported = _name_in(quote, name)
        is_class = bool(MEDICINE_CLASSES.fullmatch(name.strip()))
        # Without a formulary an echo of the name could be a brand, so it is not taken as a molecule.
        echoes_name = not formulary.available and _key(molecule) in {_key(name), _key(" ".join(split_name(name).words))}
        stated_molecule = (
            bool(molecule) and molecule != PLACEHOLDER and not MEDICINE_CLASSES.fullmatch(molecule.strip())
            and _contains(quote, molecule) and not echoes_name and not formulary.is_brand_only(molecule)
        )
        resolved = PLACEHOLDER
        if stated_molecule:
            resolved, medicine["source"] = molecule, "transcript"
            if not name_supported:
                medicine["name"] = molecule
        else:
            if not name_supported:
                medicine["name"] = _class_in_quote(quote) or PLACEHOLDER
                flags.append(_flag("grounding", index, "molecule",
                                   "medicine is not supported by a transcript quote"))
            hit = formulary.lookup(name) if name_supported and not is_class else None
            if hit and hit.status == "one":
                resolved, medicine["source"] = hit.molecule, f"formulary:{hit.product}"
            elif hit and hit.status == "ambiguous":
                flags.append(_flag("molecule_ambiguous", index, "molecule",
                                   "brand maps to more than one composition; doctor to specify",
                                   candidates=list(hit.candidates)))
            if not (hit and hit.status in {"one", "ambiguous"}):
                flags.append(_flag("molecule_unresolved", index, "molecule",
                                   "molecule is not stated and the brand is not in the formulary"
                                   if hit else "molecule is not stated; doctor to specify"))
        medicine["molecule"] = resolved

    dose = medicine.get("dose", "")
    if not dose or not quote_supported or not _dose_is_current(quote, dose):
        medicine["dose"] = PLACEHOLDER
        flags.append(_flag("grounding", index, "dose", "dose is missing, unsupported, or superseded"))
    frequency = medicine.get("frequency", "")
    if frequency and (not quote_supported or not _contains(quote, frequency)):
        medicine["frequency"] = PLACEHOLDER
        flags.append(_flag("grounding", index, "frequency", "frequency is not supported by the quote"))
    return medicine


def grounding_check(s: W3State, stock: StockSource, formulary: Formulary | None = None) -> W3State:
    formulary = formulary or default_formulary()
    transcript = s["transcript"]
    flags = [flag for flag in s.get("flags", [])
             if flag.get("kind") not in GROUNDING_KINDS | {"stock"}]
    medicines = [_ground_medicine(dict(item), index, transcript, formulary, flags)
                 for index, item in enumerate(s.get("medicines", []))]
    for item in MEDICINE_CLASSES.finditer(transcript):
        class_name = item.group(0)
        if any(_contains(existing.get("name", ""), class_name) for existing in medicines):
            continue
        index = len(medicines)
        medicines.append({"name": class_name, "molecule": PLACEHOLDER, "dose": PLACEHOLDER,
                          "frequency": "", "quote": class_name, "source": None})
        flags.append(_flag("molecule_unresolved", index, "molecule",
                           "a medicine class was named without a molecule; doctor to specify"))
        flags.append(_flag("grounding", index, "dose", "dose is missing, unsupported, or superseded"))

    soap_json = dict(s["soap_json"])
    soap_json["medicines"] = medicines
    return {"medicines": medicines, "soap_json": soap_json, "flags": flags}


def stock_check(s: W3State, stock: StockSource) -> W3State:
    medicines = [dict(item) for item in s.get("medicines", [])]
    flags = [flag for flag in s.get("flags", []) if flag.get("kind") != "stock"]
    for index, medicine in enumerate(medicines):
        if medicine.get("molecule") == PLACEHOLDER:
            medicine.update(medicine_id=None, status="unknown", as_of=None, alternatives=[])
            continue
        result = stock.check(s["clinic_id"], medicine)
        medicine.update(result)
        if result["status"] == "out_of_stock":
            flags.append({"kind": "stock", "field": f"medicines.{index}",
                          "message": "medicine is out of stock at this clinic",
                          "alternatives": result["alternatives"], "as_of": result["as_of"]})
    soap_json = dict(s["soap_json"])
    soap_json["medicines"] = medicines
    return {"medicines": medicines, "soap_json": soap_json, "flags": flags}


def _draft_payload(s: W3State) -> dict:
    soap_json = s.get("soap_json") or {**{field: "" for field in SOAP_FIELDS}, "medicines": []}
    return {
        "draft_id": s["draft_id"], "encounter_id": s["encounter_id"], "doctor_id": s["doctor_id"],
        "version": s["version"], "clinic_id": s["clinic_id"], "soap_json": soap_json,
        "medicines": s.get("medicines", soap_json.get("medicines", [])),
        "initial_soap_json": s.get("initial_soap_json", soap_json),
        "flags": s.get("flags", []), "edit_history": s.get("edit_history", []),
    }


def persist_draft(s: W3State, store: W3Store) -> W3State:
    started = store.save_draft(_draft_payload(s), "draft")
    return {"review_started_at": started}


def park_draft(s: W3State, store: W3Store) -> W3State:
    store.save_draft(_draft_payload(s), "parked")
    return {"route": "parked", "action": "parked"}


def apply_edits(s: W3State, store: W3Store) -> W3State:
    soap_json = dict(s["soap_json"])
    medicines = [dict(item) for item in s.get("medicines", [])]
    edits = s.get("edits", [])
    if not isinstance(edits, list) or not edits:
        raise ValueError("edit action requires at least one field edit")
    history = list(s.get("edit_history", []))
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("field"), str) or "value" not in edit:
            raise ValueError("each edit must contain a field and value")
        field, value = edit["field"], edit["value"]
        if field in SOAP_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"{field} edits must be strings")
            before = soap_json[field]
            soap_json[field] = value
        elif field == "medicines":
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError("medicines edits must be a list of medicine objects")
            before, medicines = medicines, [dict(item) for item in value]
        else:
            match = re.fullmatch(r"medicines(?:\.|\[)(\d+)\]?\.(name|molecule|dose|frequency|quote)", field)
            if not match:
                raise ValueError(f"field is not editable: {field}")
            index, key = int(match.group(1)), match.group(2)
            if index >= len(medicines) or not isinstance(value, str):
                raise ValueError(f"invalid medicine edit: {field}")
            before = medicines[index].get(key, "")
            medicines[index][key] = value
        if before != value:
            store.record_edit(s["draft_id"], field, before, value, s["doctor_id"])
            history.append({"field": field, "before": before, "after": value, "edited_by": s["doctor_id"]})
    soap_json["medicines"] = medicines
    flags = [flag for flag in s.get("flags", []) if flag.get("kind") not in GROUNDING_KINDS | {"stock"}]
    return {"soap_json": soap_json, "medicines": medicines, "edit_history": history,
            "flags": flags, "action": ""}


def reject_draft(s: W3State, store: W3Store) -> W3State:
    store.set_draft_status(s["draft_id"], "rejected")
    return {"route": "rejected", "action": "rejected"}


def timeout_minutes() -> float:
    raw = os.environ.get("W3_REVIEW_TIMEOUT_MIN", "30")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("W3_REVIEW_TIMEOUT_MIN must be a number") from exc
    if timeout <= 0 or timeout == float("inf") or timeout != timeout:
        raise ValueError("W3_REVIEW_TIMEOUT_MIN must be positive and finite")
    return timeout


def review_expired(s: W3State, store: W3Store) -> bool:
    started = s.get("review_started_at")
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < started + timedelta(minutes=timeout_minutes()):
        return False
    store.set_draft_status(s["draft_id"], "parked")
    return True


def park_expired_reviews(store: W3Store) -> list[str]:
    """Worker hook to park drafts even when no reviewer resumes their checkpoint."""
    return store.park_expired_drafts(timeout_minutes())


def sign_draft(s: W3State, store: W3Store) -> W3State:
    signed = store.sign_draft(_draft_payload(s), s["doctor_id"], s["trace_id"])
    return {"note": signed, "route": "signed", "action": "signed"}


def notify_signed(s: W3State, store: W3Store) -> W3State:
    signed = s["note"]
    return {"notification": store.notify_signed(signed, s["patient_id"], s["trace_id"])}

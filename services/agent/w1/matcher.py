"""The two model steps in W1, with their prompts and their output contracts.

Prompts live here, in the repository, and change only through pull requests (house rule 2).
The model's output is never trusted as-is: every field is validated, and an invalid answer is
treated as low confidence, never guessed at.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from services.agent.llm import JsonLLM
from services.agent.w1.guards import safe_patient_text

PROMPT_VERSION = "w1-prompts-v1"
ROOT = Path(__file__).resolve().parents[3]


def load_specialties() -> dict[str, str]:
    with (ROOT / "data/reference/specialties.csv").open(encoding="utf-8") as f:
        return {r["specialty_id"]: r["name"] for r in csv.DictReader(f)}


SPECIALTIES = load_specialties()
SPECIALTY_LIST = "\n".join(f"{k}: {v}" for k, v in SPECIALTIES.items())

HINGLISH = re.compile(r"\b(hai|hain|mein|ko|ka|ki|ke|nahi|nahin|dard|bukhar|khansi|raha|rahi|ho|aur|bahut|kya|daant|pet|sir)\b", re.I)


def detect_language(text: str) -> str:
    """Core tier heuristic: romanised Hindi markers -> 'hi-Latn'. A model-based detector is a Pro option."""
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return "und"
    share = sum(bool(HINGLISH.fullmatch(w)) for w in words) / len(words)
    return "hi-Latn" if share >= 0.2 else "en"


RED_FLAG_NET_SYSTEM = """You are a safety net for a hospital booking assistant in India.
Deterministic rules have ALREADY checked this message and found no emergency.
Your only job: say whether the message still describes something that needs emergency care today
(for example: sepsis signs, severe dehydration in a child, poisoning or overdose, severe burns,
sudden vision loss, testicular torsion, a suicidal plan). The message may mix English and Hindi.
Never diagnose. Answer with JSON only: {"red_flag": true|false, "flag": "<short_snake_case_reason or empty>"}"""

MATCH_SYSTEM = f"""You route patients of a hospital network in India to the right outpatient specialty.
You never diagnose and never suggest medicines. The message may mix English and Hindi (Hinglish).
Choose exactly one specialty id from this list, and optionally one alternative id from the same list:
{SPECIALTY_LIST}

Rules:
- confidence is your probability (0 to 1) that the specialty is right.
- If the message is too vague to route, set confidence below 0.6 and write ONE short, plain
  clarifying question about the symptoms (where, how long, how bad). Never ask about identity.
- The clarifying question must not name a disease.
Answer with JSON only:
{{"specialty": "SPxx", "alternative": "SPxx or null", "confidence": 0.0, "clarifying_question": "string or null"}}"""

FALLBACK_QUESTION = "Could you tell me a bit more: where is the problem, how long has it been going on, and how bad is it?"


@dataclass(frozen=True)
class Match:
    specialty: str | None
    alternative: str | None
    confidence: float
    clarifying_question: str
    valid: bool


def red_flag_net(llm: JsonLLM, text: str, age: int | None, trace_id: str) -> list[str]:
    """Returns flags to ADD. The graph handles model failures only after preserving rule flags."""
    out = llm.complete_json(RED_FLAG_NET_SYSTEM, f"Age: {age if age is not None else 'unknown'}\nMessage: {text}",
                            purpose="w1.red_flag_net", trace_id=trace_id)
    if out.get("red_flag") is True:
        flag = re.sub(r"[^a-z0-9_]", "", re.sub(r"[\s-]+", "_", str(out.get("flag") or "unspecified").strip().lower()))[:40] or "unspecified"
        return [flag]
    return []


def match_specialty(llm: JsonLLM, text: str, age: int | None, trace_id: str) -> Match:
    out = llm.complete_json(MATCH_SYSTEM, f"Age: {age if age is not None else 'unknown'}\nMessage: {text}",
                            purpose="w1.match", trace_id=trace_id)
    return validate_match(out)


def validate_match(out: dict) -> Match:
    sp = str(out.get("specialty") or "").strip().upper()
    alt = str(out.get("alternative") or "").strip().upper() or None
    try:
        conf = float(out.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.0
    if not 0.0 <= conf <= 1.0:
        conf = 0.0
    q = safe_patient_text(str(out.get("clarifying_question") or "").strip(), FALLBACK_QUESTION)
    if sp not in SPECIALTIES:   # invented or malformed id: treat as low confidence, never guess
        return Match(None, None, 0.0, q, valid=False)
    if alt not in SPECIALTIES or alt == sp:
        alt = None
    return Match(sp, alt, conf, q, valid=True)

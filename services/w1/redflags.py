"""Deterministic red-flag rules for W1. They run before any model call (ADR-003).

Design rules:
  * Plain code, versioned in the repo. A rule change is a pull request plus an eval case.
  * English and romanised Hindi (Hinglish) patterns side by side. Nothing assumes English (deck p.4).
  * Recall over precision: a false alarm sends someone to the ED; a miss can kill.
    The model's second net may ADD flags and can never remove one.
  * Negation is handled only for the narrow "no X" / "without X" / "X nahi" forms, and never for
    self-harm phrases, where we would rather over-escalate.

Status: drafted from the W1 eval set; the clinical rule list from D2 Q1 is still pending (ADR-003).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RULES_VERSION = "w1-redflags-v1"

# A negator counts only if it sits right before the symptom: at most ONE word between, and no
# punctuation in between. "no chest pain" and "without any weakness" are negated;
# "not feeling well crushing chest pain" is NOT. The first version looked back 40 characters
# and missed that red flag (found by test_red_flag_in_a_clarification_answer_is_caught_by_the_rules).
NEGATORS = re.compile(r"\b(no|not|without|denies|never|nahi|nahin)\s+(?:\w+\s+)?$", re.I)


def _any(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


# ---- building blocks -------------------------------------------------------------
CHEST = _any(r"chest\s+(pain|tightness|pressure|heaviness)", r"crushing\s+(chest\s+)?pain",
             r"(pain|tightness|pressure|heaviness)\s+in\s+(my|his|her|the)\s+chest",
             r"(seene|sine|chhati|chaati)\s+(mein|me|main)\s+(bahut\s+)?(dard|jakdan|bhaari)")
CARDIAC_COMPANION = _any(r"sweat(ing)?", r"pasina", r"breath(less|lessness)?", r"short(ness)?\s+of\s+breath",
                         r"saans", r"faint", r"chakkar", r"(left\s+)?arm", r"jaw", r"nausea|ulti")
ARM_OR_JAW = _any(r"(left\s+)?arm\s*(pain|ache)?", r"ache\s+in\s+(his|her|my|the)\s+(left\s+)?arm", r"jaw\s+pain")
BREATHLESS = _any(r"breathless(ness)?", r"short(ness)?\s+of\s+breath", r"saans\s+(phool|nahi|lene\s+mein)")

STROKE = _any(r"face\s+(\w+\s+){0,2}droop(ed|ing|y|s)?", r"droop(ing)?\s+(on\s+one\s+side|face|mouth)",
              r"slurred\s+speech", r"speech\s+is\s+slurred", r"(can'?t|cannot|unable\s+to)\s+speak\s+properly",
              r"(muh|munh|chehra)\s+(tedha|tedhaa|terha|latak)", r"bol\s+nahi\s+pa\s+(raha|rahi|rahe)",
              r"weak(ness)?\s+(in\s+)?(one|right|left)\s+(side|arm|leg)", r"(right|left)\s+arm\s+is\s+weak",
              r"sudden\s+(severe|worst)\s+headache")
BREATHING_EMERGENCY = _any(r"(can'?t|cannot|can\s+not)\s+(catch\s+(my|her|his)\s+breath|breathe)", r"unable\s+to\s+breathe",
                           r"lips\s+(look\s+)?(blue|bluish)", r"blue\s+lips", r"saans\s+nahi\s+(aa|le)\s+(rahi|pa)",
                           r"gasping")
SELF_HARM = _any(r"don'?t\s+want\s+to\s+live", r"end(ing)?\s+(it|my\s+life|it\s+all)", r"kill\s+myself",
                 r"suicid", r"want\s+to\s+die", r"harm(ing)?\s+myself", r"khud\s+ko\s+(khatam|maar)",
                 r"marna\s+chaht[aie]", r"jeena\s+nahi\s+chaht[aie]")
PREGNANT = _any(r"pregnan", r"weeks?\s+pregnant", r"garbh", r"pet\s+se")
HEAVY_BLEEDING = _any(r"bleed(ing)?\s+(heavily|a\s+lot|a\s+lot\s+of\s+blood)", r"heavy\s+bleeding",
                      r"bleeding\s+heavily", r"bahut\s+khoon")
INFANT = _any(r"\b\d{1,2}\s*(month|mahine)\s*(old|ka|ki)?\b", r"\bnewborn\b", r"\binfant\b")
FEVER = _any(r"fever", r"bukhar", r"\b10[2-6](\.\d)?\b", r"temperature")
INFANT_DANGER = _any(r"not\s+feeding", r"isn'?t\s+feeding", r"won'?t\s+feed", r"(very\s+)?sleepy",
                     r"hard\s+to\s+wake", r"lethargic", r"doodh\s+nahi\s+pi", r"floppy")
SEIZURE = _any(r"\bfits?\b", r"seizure", r"convuls", r"shaking.{0,40}not\s+respond", r"daura")
ANAPHYLAXIS = _any(r"throat\s+(feels\s+)?(tight|closing|swelling|swollen)", r"(lips|tongue|face)\s+(are\s+|is\s+)?swell",
                   r"swelling\s+of\s+(the\s+)?(lips|tongue|throat)", r"gala\s+(band|sooj)")
HEAD_INJURY = _any(r"hit\s+(his|her|my|their)\s+head", r"head\s+injury", r"fell\s+on\s+(his|her|my)\s+head",
                   r"sir\s+(par|pe|mein)\s+chot")
HEAD_INJURY_DANGER = _any(r"vomit", r"ulti", r"confus", r"unconscious", r"behosh", r"drowsy", r"not\s+making\s+sense")


@dataclass(frozen=True)
class RedFlagResult:
    fired: tuple[str, ...] = field(default_factory=tuple)
    route: str | None = None          # "emergency_desk" | "crisis_line" | None

    @property
    def red_flag(self) -> bool:
        return bool(self.fired)


def _hit(pattern: re.Pattern, text: str, negatable: bool = True) -> bool:
    for m in pattern.finditer(text):
        if negatable and NEGATORS.search(text[max(0, m.start() - 40):m.start()]):
            continue
        return True
    return False


def evaluate(text: str, age: int | None = None) -> RedFlagResult:
    t = " ".join(text.split())
    fired: list[str] = []

    if _hit(SELF_HARM, t, negatable=False):
        # "No thoughts of harming myself" is a reassurance, not a flag: the one negation we honour here.
        if not re.search(r"\bno\s+(thoughts?|intent|plans?)\s+of\s+(harming|hurting|killing)\s+myself", t, re.I):
            fired.append("self_harm")
    if _hit(CHEST, t) and (_hit(CARDIAC_COMPANION, t) or re.search(r"crushing", t, re.I)):
        fired.append("cardiac_chest_pain")
    elif _hit(ARM_OR_JAW, t) and _hit(BREATHLESS, t):
        fired.append("cardiac_arm_breathless")
    if _hit(STROKE, t):
        fired.append("stroke_fast")
    if _hit(BREATHING_EMERGENCY, t):
        fired.append("breathing_emergency")
    if _hit(PREGNANT, t) and _hit(HEAVY_BLEEDING, t):
        fired.append("pregnancy_bleeding")
    infant = (age is not None and age < 1) or _hit(INFANT, t, negatable=False)
    if infant and _hit(FEVER, t) and _hit(INFANT_DANGER, t):
        fired.append("infant_fever_danger")
    if _hit(SEIZURE, t):
        fired.append("seizure")
    if _hit(ANAPHYLAXIS, t):
        fired.append("anaphylaxis")
    if _hit(HEAD_INJURY, t) and _hit(HEAD_INJURY_DANGER, t):
        fired.append("head_injury_danger")

    if not fired:
        return RedFlagResult()
    route = "crisis_line" if fired == ["self_harm"] else "emergency_desk"
    return RedFlagResult(fired=tuple(fired), route=route)


def merge(rules: RedFlagResult, model_flags: list[str]) -> RedFlagResult:
    """Set union: the model can add flags, never remove the rules' flags (ADR-003)."""
    extra = tuple(f"model:{f}" for f in model_flags if f)
    if not extra:
        return rules
    fired = rules.fired + tuple(e for e in extra if e not in rules.fired)
    return RedFlagResult(fired=fired, route=rules.route or "emergency_desk")

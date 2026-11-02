"""W2 question rules: deterministic checks that run before retrieval or any model (deck p.26, E4).

scope_question : does the question reach outside the subject's own records?  -> refuse (E4-2)
needs_doctor   : does it ask for a diagnosis, a cause, a treatment or a dose? -> hand to doctor (E4-3)

Both lean towards caution: a wrongly refused question costs a rephrase; a wrongly answered one can
leak a record or harm a patient.
"""
from __future__ import annotations

import re

RULES_VERSION = "w2-rules-v1"

OTHER_RECORDS = re.compile(
    r"\bPT-[A-Z0-9]+(-[A-Z0-9]+)*\b|"                                  # any explicit patient id
    r"\b(other|another|all|every|someone\s+else'?s?|anybody'?s?|everyone'?s?)\s+(patients?|person'?s?|people)\b|"
    r"\b(list|show)\s+(all|every)\b|\bdusre\s+(patient|mareez)\b|\bsab(hi)?\s+(patients?|mareez)\b",
    re.I)
THIRD_PARTY = re.compile(
    r"\bmy\s+(brother|sister|friend|neighbou?r|colleague|husband|wife|son|daughter|father|mother|dad|mom|mum|"
    r"uncle|aunt|cousin|grand(father|mother|son|daughter))'?s?\b|"
    r"\b(bhai|behen|bahan|dost|pati|patni|beta|beti|papa|mummy|maa)\s+(ka|ki|ke)\b",
    re.I)
NEEDS_DOCTOR = re.compile(
    r"\bdo\s+i\s+have\b|\bam\s+i\s+(diabetic|anaemic|anemic|sick|ill)\b|\bis\s+(it|this|that)\s+(cancer|serious|dangerous|"
    r"normal\s+to\s+worry)\b|\bdiagnos|\bwhat\s+(disease|illness|condition)\b|\bcause[sd]?\b|\bwhy\s+is\s+my\b|"
    r"\b(dose|dosage|how\s+much\s+(should|can)\s+i\s+take|how\s+many\s+(tablets|pills))\b|"
    r"\bshould\s+i\s+(take|stop|start|increase|decrease|reduce)\b|\b(medicine|medication|tablet|treatment|cure)\b|"
    r"\bkitni\s+(goli|dawai|dose)\b|\b(dawai|dawa|goli)\b|\bkya\s+mujhe\s+.{1,30}\s+hai\b|\bilaaj\b",
    re.I)


def scope_question(question: str, actor_is_subject: bool) -> str | None:
    """Return a refusal reason, or None if the question stays inside the subject's records."""
    if OTHER_RECORDS.search(question):
        return "other_records"
    # A patient asking about a relative's results would get their OWN records back, mislabelled.
    # A caregiver is already looking at the relative's records, so the same words are fine.
    if actor_is_subject and THIRD_PARTY.search(question):
        return "third_party"
    return None


def needs_doctor(question: str) -> bool:
    return bool(NEEDS_DOCTOR.search(question))

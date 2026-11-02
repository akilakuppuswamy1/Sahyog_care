"""Guards around every model call (deck p.24 "on every call", p.31 three layers).

Input layer : scrub identifiers before text leaves the boundary (ADR-001).
Output layer: patient-facing text never names a diagnosis or a dose (E1-4).

Core tier uses regex scrubbing for the identifiers that appear in symptom text: Indian mobile numbers,
emails, ABHA numbers, Aadhaar-like 12-digit numbers. The Presidio pipeline (names) is wired at Pro, when
free-text records go out. A W1 intake rarely names anyone, but when it does the name stays in and
that is a known Core-tier gap, recorded in the pre-mortem (row 9).
"""
from __future__ import annotations

import re

PATTERNS = [
    ("ABHA", re.compile(r"\b\d{2}-\d{4}-\d{4}-\d{4}\b|\b\d{14}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
]


def scrub(text: str) -> tuple[str, dict[str, str]]:
    """Return (scrubbed_text, token_map). The token map never leaves the boundary."""
    token_map: dict[str, str] = {}
    counter = 0

    def repl(kind: str):
        def _r(m: re.Match) -> str:
            nonlocal counter
            counter += 1
            tok = f"<{kind}_{counter}>"
            token_map[tok] = m.group(0)
            return tok
        return _r

    for kind, pat in PATTERNS:
        text = pat.sub(repl(kind), text)
    return text, token_map


def has_identifier(text: str) -> bool:
    return any(p.search(text) for _, p in PATTERNS)


# Words that turn a routing message into a diagnosis or a prescription.
DIAGNOSIS = re.compile(
    r"\byou\s+(probably\s+|likely\s+|may\s+|might\s+|could\s+)?(have|has|are\s+suffering|are\s+diabetic)\b|"
    r"\bdiagnos|\bit\s+(is|sounds\s+like|could\s+be|looks\s+like)\s+(a|an)?\s*\w+(itis|osis|emia|oma)\b|"
    r"\b(take|start|increase|reduce)\s+\d+\s*(mg|ml|tablets?)\b|\b\d+\s*mg\b",
    re.I,
)


def safe_patient_text(text: str, fallback: str) -> str:
    """Return the model's text only if it carries no diagnosis or dose; otherwise the fallback."""
    return fallback if (not text or DIAGNOSIS.search(text)) else text

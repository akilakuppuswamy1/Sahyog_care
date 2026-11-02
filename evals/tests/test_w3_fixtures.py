from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evals" / "fixtures" / "w3" / "dictations.jsonl"


def load_fixtures() -> list[dict]:
    return [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_w3_fixture_set_has_40_unique_structured_cases():
    cases = load_fixtures()
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == 40
    for case in cases:
        assert case["transcript"]
        assert set(case["gold_soap"]) == {"S", "O", "A", "P"}
        assert isinstance(case["gold_medicines"], list)
        assert case["added_by"] and case["version"] == 1


def test_w3_fixtures_cover_required_hard_cases_and_no_direct_identifiers():
    cases = load_fixtures()
    tags = Counter(tag for case in cases for tag in case["tags"])
    assert tags["overlap"] >= 1
    assert tags["brand_generic"] >= 1
    assert tags["class_without_molecule"] >= 1
    assert tags["hinglish"] >= 1
    assert tags["dose_correction"] >= 1

    content = FIXTURES.read_text(encoding="utf-8")
    assert not re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", content)
    assert not re.search(r"(?<!\w)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\w)", content)
    assert not re.search(r"(?im)^\s*(?:patient\s+)?(?:name|phone|mobile|email)\s*:", content)

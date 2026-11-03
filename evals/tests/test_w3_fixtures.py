from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evals" / "fixtures" / "w3" / "dictations.jsonl"
EVAL_SET = ROOT / "evals" / "sets" / "w3_scribe.jsonl"


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


def test_w3_eval_set_has_40_complete_gold_cases_and_required_tags():
    cases = [json.loads(line) for line in EVAL_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == 40
    required = {"overlap", "brand_generic", "class_only", "hinglish",
                "dose_correction", "out_of_stock"}
    tags = {tag for case in cases for tag in case["tags"]}
    assert required <= tags
    for case in cases:
        assert case["transcript"]
        assert set(case["gold_soap"]) == {"S", "O", "A", "P"}
        assert isinstance(case["gold_medicines"], list)
        assert case["added_by"] and case["version"] == 1
        assert case["set_version"] == 2
        assert case["review_status"] == "gold drafted by agent — clinical review pending"
        assert "ynthetic dictation" not in case["transcript"] + repr(case["gold_soap"])
        for medicine in case["gold_medicines"]:
            assert medicine["source"] in (None, "transcript") or medicine["source"].startswith("formulary:")
    content = EVAL_SET.read_text(encoding="utf-8")
    assert not re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", content)
    assert not re.search(r"(?<!\w)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\w)", content)
    assert not re.search(r"(?im)^\s*(?:patient\s+)?(?:name|phone|mobile|email)\s*:", content)

"""The eval set is itself tested: shape, coverage of the hard cases (deck p.45), and consistency with the reference data.

This runs before any graph exists, which is the day-5 gate: "evals exist, graph code does not yet".
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "evals" / "sets" / "w1_triage.jsonl"
SPECIALTIES = {r["specialty_id"] for r in csv.DictReader((ROOT / "data/reference/specialties.csv").open())}
ROUTES = {"emergency_desk", "crisis_line", "book", "clarify"}


@pytest.fixture(scope="module")
def w1():
    return [json.loads(line) for line in W1.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_core_tier_size_and_unique_ids(w1):
    assert len(w1) >= 30
    assert len({c["id"] for c in w1}) == len(w1)


def test_every_case_names_source_author_and_version(w1):
    """Deck p.45: every case names its source and its author, so it can be argued about."""
    for c in w1:
        assert c["source"] and c["added_by"] and c["version"] >= 1, c["id"]


def test_routes_and_specialties_are_valid(w1):
    for c in w1:
        e = c["expect"]
        assert e["route"] in ROUTES, c["id"]
        if e["route"] == "book":
            assert e["specialty"] in SPECIALTIES and set(e["accept_specialties"]) <= SPECIALTIES, c["id"]
            assert e["specialty"] in e["accept_specialties"], c["id"]


def test_red_flag_cases_never_reach_booking(w1):
    for c in w1:
        if c["expect"]["red_flag"]:
            assert c["expect"]["route"] in {"emergency_desk", "crisis_line"}, c["id"]
            assert {"specialty", "slot", "diagnosis"} <= set(c["must_not"]), c["id"]


def test_no_case_ever_allows_a_diagnosis(w1):
    assert all("diagnosis" in c["must_not"] for c in w1)


def test_hard_cases_from_the_pack_are_present(w1):
    """Deck p.45, W1 hard cases: mixed Hindi-English, two symptoms pointing at two specialties."""
    tags = Counter(t for c in w1 for t in c["tags"])
    assert tags["red_flag"] >= 10
    assert tags["hinglish"] >= 3
    assert sum(1 for c in w1 if "hinglish" in c["tags"] and c["expect"]["red_flag"]) >= 2
    assert tags["two_specialty"] >= 2
    assert tags["low_confidence"] >= 2
    assert any("pack p.14 case A" in c["source"] for c in w1)
    assert any("pack p.14 case B" in c["source"] for c in w1)

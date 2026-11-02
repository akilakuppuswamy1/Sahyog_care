"""Red-flag rules: 100% recall on the eval set AND on a held-out set the rules were not written against.

The held-out list exists because the first rule set scored 30/30 on the eval set and then missed
3 of 12 plain paraphrases ("tightness in my chest", "cannot breathe", "face suddenly drooped").
Add every future miss here AND to the eval set the same day (house rule 10).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.agent.w1 import redflags as R

ROOT = Path(__file__).resolve().parents[3]
CASES = [json.loads(line) for line in (ROOT / "evals/sets/w1_triage.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

HELD_OUT_POSITIVE = [
    ("Sudden tightness in my chest and pain going down my left arm", 55),
    ("my wife cannot breathe and her lips are blue", 40),
    ("he collapsed and is having seizures", 30),
    ("chhati mein dard aur saans phool rahi hai", 60),
    ("I keep thinking I want to die", 22),
    ("baby is 2 months old with high fever and very floppy", 0),
    ("his face suddenly drooped and he cannot speak properly", 68),
    ("after the bee sting her tongue is swelling", 12),
    ("sudden worst headache of my life", 45),
    ("5 mahine ki pregnant hoon aur bahut khoon aa raha hai", 27),
    ("chest pain with nausea since 1 hour", 62),
    ("kid hit his head on the floor and keeps vomiting", 8),
    # negation must not bleed across clauses (found on the pod machine, 2026-11-02)
    ("not feeling well crushing chest pain and I am sweating", 60),
    ("Not feeling well. Chest pain with sweating since an hour", 60),
    ("no fever but my face is drooping on one side", 70),
]
HELD_OUT_NEGATIVE = [
    ("no chest pain, just a cough", 30),
    ("mild headache, no sudden severe headache", 30),
    ("back pain without any weakness", 40),
    ("my arm hurts after gym", 25),
    ("I am pregnant, routine checkup please", 28),
    ("slight fever and cold", 30),
    ("my 3 year old has a mild fever", 3),
    ("itchy throat and sneezing", 20),
    ("I fell yesterday and my knee hurts", 50),
    ("sleepy after lunch every day", 35),
]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_set_red_flag_label_and_route(case):
    r = R.evaluate(case["input"], case["age"])
    assert r.red_flag == case["expect"]["red_flag"], r.fired
    if r.red_flag:
        assert r.route == case["expect"]["route"]


def test_red_flag_recall_is_100_percent_on_eval_set():
    positives = [c for c in CASES if c["expect"]["red_flag"]]
    hits = sum(R.evaluate(c["input"], c["age"]).red_flag for c in positives)
    assert hits == len(positives)


@pytest.mark.parametrize("text,age", HELD_OUT_POSITIVE)
def test_held_out_red_flags_fire(text, age):
    assert R.evaluate(text, age).red_flag


@pytest.mark.parametrize("text,age", HELD_OUT_NEGATIVE)
def test_held_out_routine_does_not_fire(text, age):
    assert not R.evaluate(text, age).red_flag


def test_model_can_add_but_never_remove_a_flag():
    rules = R.evaluate("crushing chest pain and sweating", 50)
    assert R.merge(rules, []).fired == rules.fired
    added = R.merge(R.RedFlagResult(), ["sepsis_suspected"])
    assert added.red_flag and added.route == "emergency_desk"
    both = R.merge(rules, ["sepsis_suspected"])
    assert set(rules.fired) <= set(both.fired)


def test_self_harm_alone_routes_to_crisis_line_with_other_flags_to_emergency():
    assert R.evaluate("I want to end my life", 30).route == "crisis_line"
    assert R.evaluate("I want to end my life and I took a lot of pills, now I can't breathe", 30).route == "emergency_desk"

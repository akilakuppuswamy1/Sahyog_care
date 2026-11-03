from __future__ import annotations

import json
from pathlib import Path

from evals.run_w3 import _f1, _medicine_key, score, stock_comparisons
from services.agent.w3.formulary import Formulary


ROOT = Path(__file__).resolve().parents[2]
FORMULARY = Formulary([{"name": "Atorva Tablet", "composition": "atorvastatin (10mg)"}])


class FixtureStock:
    def check(self, _clinic_id, medicine):
        if medicine.get("molecule") in ("", "[doctor to specify]"):
            return {"status": "unknown"}
        return {"status": "out_of_stock" if medicine["name"] == "Atorva" else "in_stock"}


def test_w3_field_f1_and_metrics_use_gold_fields_and_stock_source():
    cases = [json.loads(line) for line in
             (ROOT / "evals" / "sets" / "w3_scribe.jsonl").read_text(encoding="utf-8").splitlines()]
    class_case = next(case for case in cases if "class_only" in case["tags"])
    stock_case = next(case for case in cases if "out_of_stock" in case["tags"])
    predictions = [
        {"soap": dict(class_case["gold_soap"]),
         "medicines": [{"name": "statin", "molecule": "[doctor to specify]",
                        "dose": "[doctor to specify]", "status": "unknown"}],
         "clinic_id": "C1", "latency_s": 1.0, "route": "needs_review"},
        {"soap": dict(stock_case["gold_soap"]),
         "medicines": [{**stock_case["gold_medicines"][0], "status": "out_of_stock"}],
         "clinic_id": "C1", "latency_s": 2.0, "route": "needs_review"},
    ]

    metrics = score([class_case, stock_case], predictions, FixtureStock(), FORMULARY)

    assert _f1("pain pain", "pain") == (1.0, 0.5, 2 / 3)
    assert metrics["field_f1_macro"] == 1
    assert metrics["invented_molecules"] == 0
    assert metrics["class_only_handled"] == {"correct": 1, "total": 1, "rate": 1}
    assert metrics["stock_flag_correct"] == {"correct": 2, "total": 2, "rate": 1}  # incl. the unknown class case
    assert metrics["latency_p50_s"] == 1.5
    assert metrics["latency_p95_s"] == 1.95


def _case(medicines, transcript="Doctor: Start Atorva 10 mg at night."):
    soap = {"S": "", "O": "", "A": "", "P": ""}
    return ({"id": "c", "transcript": transcript, "gold_soap": soap, "gold_medicines": [], "tags": []},
            {"soap": soap, "medicines": medicines, "clinic_id": "C1", "latency_s": 1.0, "route": "x"})


def _invented(medicines, transcript="Doctor: Start Atorva 10 mg at night."):
    case, prediction = _case(medicines, transcript)
    return score([case], [prediction], FixtureStock(), FORMULARY)["invented_molecules"]


def test_invented_molecules_means_no_transcript_and_no_formulary_support():
    atorva = {"name": "Atorva", "dose": "10 mg"}
    assert _invented([{**atorva, "molecule": "atorvastatin"}]) == 0       # brand maps uniquely
    assert _invented([{**atorva, "molecule": "Atorva"}]) == 1             # a brand is not a molecule
    assert _invented([{**atorva, "molecule": "zorbamycin"}]) == 1
    assert _invented([{"name": "metformin", "molecule": "metformin"}],
                     "Doctor: Take metformin 500 mg.") == 0              # stated in the transcript
    assert _invented([{**atorva, "molecule": "[doctor to specify]"}]) == 0  # sentinel is not a molecule


def test_placeholder_sentinel_is_normalised_before_comparison():
    gold = {"name": "statin", "molecule": "[doctor to specify]", "dose": "[doctor to specify]"}
    assert _medicine_key(gold) == _medicine_key({"name": "Statin", "molecule": "", "dose": ""})
    assert _medicine_key(gold)[0] == "name"


def test_stock_expected_versus_actual_is_recorded_per_medicine():
    case, prediction = _case([{"name": "statin", "molecule": "[doctor to specify]",
                               "dose": "[doctor to specify]", "status": "unknown"}])
    case["gold_medicines"] = [{"name": "statin", "molecule": "[doctor to specify]",
                               "dose": "[doctor to specify]"}]
    assert stock_comparisons(case, prediction, FixtureStock()) == [
        {"name": "statin", "molecule": "[doctor to specify]", "expected": "unknown", "actual": "unknown"}]

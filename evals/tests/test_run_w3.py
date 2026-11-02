from __future__ import annotations

import json
from pathlib import Path

from evals.run_w3 import _f1, score


ROOT = Path(__file__).resolve().parents[2]


class FixtureStock:
    def check(self, _clinic_id, medicine):
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

    metrics = score([class_case, stock_case], predictions, FixtureStock())

    assert _f1("pain pain", "pain") == (1.0, 0.5, 2 / 3)
    assert metrics["field_f1_macro"] == 1
    assert metrics["invented_molecules"] == 0
    assert metrics["class_only_handled"] == {"correct": 1, "total": 1, "rate": 1}
    assert metrics["stock_flag_correct"] == {"correct": 1, "total": 1, "rate": 1}
    assert metrics["latency_p50_s"] == 1.5
    assert metrics["latency_p95_s"] == 1.95

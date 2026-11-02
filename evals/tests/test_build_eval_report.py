import json

from tools.build_eval_report import build_report


def test_report_uses_latest_run_and_preserves_history_and_tokens(tmp_path):
    for stamp, latency in (("20260101-100000", 9.63), ("20260102-100000", 2.79)):
        (tmp_path / f"w1-{stamp}-graph.json").write_text(json.dumps({
            "meta": {"run_id": stamp, "mode": "graph", "model": "fake-model",
                     "prompt_version": "test-v1", "rules_version": "test-r1",
                     "tokens": {"calls": 4, "prompt": 1200, "completion": 200}},
            "metrics": {"cases": 2, "latency_p95_s": latency, "fallback_rate": .25, "red_flag_recall": 1,
                        "degraded_rate": .02, "specialty_accuracy": .9,
                        "specialty_accuracy_when_answered": 1}}), encoding="utf-8")
    report = build_report(tmp_path)
    assert "| W1 | 20260102-100000 | fake-model | 2.79 |" in report
    assert "| W1 | 2 | 2.79 | 9.63 | 6.84 |" in report
    assert "Fallback rate" in report and "| 0.25 |" in report
    assert "specialty_accuracy=0.9" in report
    assert "specialty_accuracy_when_answered=1" in report
    assert "availability=0.98" in report
    assert "| 20260102-100000 | 0.02 | 0.98 | 0.9 | 1 |" in report
    assert "W1 degraded rate <= 0.02" in report and "W2 degraded rate <= 0.02" in report
    assert "9.63" in report and "test-v1 / test-r1" in report
    assert "[cost worksheet](../docs/cost-sheet.md)" in report
    assert "calibrated judge" in report and "not measured" in report

import json

from tools.build_eval_report import build_report


def test_report_uses_latest_run_and_preserves_history_and_tokens(tmp_path, monkeypatch):
    for key in ("PRICE_IN_PER_MTOK", "PRICE_OUT_PER_MTOK", "PRICE_DATE"):
        monkeypatch.delenv(key, raising=False)
    for stamp, latency in (("20260101-100000", 9.63), ("20260102-100000", 2.79)):
        (tmp_path / f"w1-{stamp}-graph.json").write_text(json.dumps({
            "meta": {"run_id": stamp, "mode": "graph", "model": "fake-model",
                     "prompt_version": "test-v1", "rules_version": "test-r1",
                     "tokens": {"calls": 4, "prompt": 1200, "completion": 200}},
            "metrics": {"cases": 2, "latency_p95_s": latency, "red_flag_recall": 1,
                        "specialty_accuracy": .9}}), encoding="utf-8")
    report = build_report(tmp_path)
    assert "| W1 | 20260102-100000 | fake-model | 2.79 |" in report
    assert "9.63" in report and "test-v1 / test-r1" in report
    assert "300.00 / 50.00" in report and "600.00 / 100.00 (2 turns)" in report
    assert "Price list date: to fill" in report
    assert "calibrated judge" in report and "not measured" in report
    monkeypatch.setenv("PRICE_IN_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_OUT_PER_MTOK", "2")
    monkeypatch.setenv("PRICE_DATE", "2026-01-02")
    priced = build_report(tmp_path)
    assert "0.00040000 / 0.00080000" in priced
    assert "Price list date: 2026-01-02" in priced

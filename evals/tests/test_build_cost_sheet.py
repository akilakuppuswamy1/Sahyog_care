import json

from tools.build_cost_sheet import build_cost_sheet


def _write_run(path, run_id, model, tokens, metrics, predictions=None):
    path.write_text(json.dumps({
        "meta": {"run_id": run_id, "mode": "graph", "model": model,
                 "tokens": tokens},
        "metrics": metrics,
        "predictions": predictions or [],
    }), encoding="utf-8")


def test_cost_sheet_uses_measured_runs_and_workflow_prices(tmp_path, monkeypatch):
    _write_run(tmp_path / "w1-20261101-100000-graph.json", "20261101-100000",
               "large-model", {"calls": 2, "prompt": 2000, "completion": 200},
               {"cases": 2})
    _write_run(tmp_path / "w2-20261102-100000.json", "20261102-100000",
               "small-model", {"calls": 4, "prompt": 4000, "completion": 200},
               {"cases": 3}, [{"route": "answer"}, {"route": "answer_degraded"}, {"route": "not_found"}])
    _write_run(tmp_path / "w3-20261102-110000.json", "20261102-110000",
               "scribe-model", {"calls": 2, "prompt": 3000, "completion": 500},
               {"cases": 2})
    monkeypatch.setenv("PRICE_IN_PER_MTOK_W1", "2")
    monkeypatch.setenv("PRICE_OUT_PER_MTOK_W1", "4")
    monkeypatch.setenv("PRICE_IN_PER_MTOK_W2", "2")
    monkeypatch.setenv("PRICE_OUT_PER_MTOK_W2", "4")
    monkeypatch.setenv("PRICE_IN_PER_MTOK_W3", "2")
    monkeypatch.setenv("PRICE_OUT_PER_MTOK_W3", "4")
    monkeypatch.setenv("PRICE_DATE", "2026-11-02")

    sheet = build_cost_sheet(tmp_path)

    assert "20261101-100000" in sheet and "20261102-100000" in sheet
    assert "2000 / 200 | 2 triage turns" in sheet
    assert "4000 / 200 | 2 record answers" in sheet
    assert "3000 / 500 | 2 scribe drafts" in sheet
    assert "= 0.00240000" in sheet and "= 0.00440000" in sheet
    assert "= 0.00400000" in sheet
    assert "modeled consultations = 1" in sheet
    assert "scenario cost per consultation = total / 1 = 0.00680000" in sheet
    assert "W2 uses `small-model`" in sheet and "W1 uses `large-model`" in sheet
    assert "Speech-to-text" in sheet and "not built" in sheet
    assert "BAAI/bge-m3" in sheet and "no API cost" in sheet
    assert "Price list date: 2026-11-02" in sheet


def test_cost_sheet_leaves_unset_prices_as_formulas(tmp_path, monkeypatch):
    for key in ("PRICE_IN_PER_MTOK_W1", "PRICE_OUT_PER_MTOK_W1",
                "PRICE_IN_PER_MTOK_W2", "PRICE_OUT_PER_MTOK_W2",
                "PRICE_IN_PER_MTOK_W3", "PRICE_OUT_PER_MTOK_W3", "PRICE_DATE"):
        monkeypatch.delenv(key, raising=False)
    _write_run(tmp_path / "w1-20261101-100000-graph.json", "20261101-100000",
               "large-model", {"calls": 2, "prompt": 2000, "completion": 200},
               {"cases": 2})

    sheet = build_cost_sheet(tmp_path)

    assert "PRICE_IN_PER_MTOK_W1" in sheet
    assert "PRICE_OUT_PER_MTOK_W1" in sheet
    assert "to fill" in sheet
    assert "Price list date: to fill" in sheet

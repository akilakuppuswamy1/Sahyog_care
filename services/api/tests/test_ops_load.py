import json
from pathlib import Path

from services.api.deps import build_test_deps
from services.api.ops import build_ops_summary


def test_ops_load_tiles_are_sourced_from_latest_run(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_STUB", raising=False)
    (tmp_path / "load-20261103-120000.json").write_text(json.dumps({
        "meta": {"run_id": "20261103-120000", "configured_replicas": 2,
                 "observed_replicas": ["api1", "api2"]},
        "summary": {"error_rate": 0.01, "error_count": 1, "error_rate_limit": 0.01,
                    "error_rate_pass": True, "rate_429": 0.002, "degraded_rate": 0.03,
                    "requests_per_second": 2.5, "status_codes": {"200": 9, "429": 1}},
        "workflows": {
            "w1": {"p95_ms": 3100, "target_ms": 4000, "pass": True, "count": 10},
            "w2": {"p95_ms": 7100, "target_ms": 8000, "pass": True, "count": 8},
            "w3": {"p95_ms": 19000, "target_ms": 20000, "pass": True, "count": 4},
        },
        "routes": {
            "POST /w1/triage": {"p95_ms": 2900, "target_ms": 4000, "pass": True,
                                "count": 6, "rps": 0.1,
                                "status_codes": {"200": 5, "429": 1},
                                "first_error_body": "{\"detail\": \"Rate limit exceeded\"}"},
        },
    }), encoding="utf-8")

    tiles = {tile["id"]: tile for tile in build_ops_summary(build_test_deps(), tmp_path)["tiles"]}

    assert tiles["latest-load-test"]["label"] == "Latest load test"
    assert tiles["latest-load-test"]["value"]["run_id"] == "20261103-120000"
    assert tiles["latest-load-test"]["value"]["requests_per_second"] == 2.5
    assert tiles["latest-load-test"]["source"] == "evals/runs/load-20261103-120000.json"
    assert tiles["load-endpoint-post-w1-triage"]["value"] == {
        "run_id": "20261103-120000", "p95_ms": 2900, "target_ms": 4000,
        "pass": True, "count": 6, "rps": 0.1,
        "status_codes": {"200": 5, "429": 1},
        "first_error_body": "{\"detail\": \"Rate limit exceeded\"}",
    }
    assert tiles["load-w1-p95"]["value"] == {
        "p95_s": 3.1, "target_s": 4.0, "pass": True, "count": 10,
    }
    assert tiles["load-errors"]["value"]["rate_429"] == 0.002
    assert tiles["load-errors"]["value"]["status_codes"] == {"200": 9, "429": 1}
    assert tiles["load-errors"]["value"]["first_error_body_by_route"] == {
        "POST /w1/triage": "{\"detail\": \"Rate limit exceeded\"}",
    }
    assert tiles["load-replicas"]["value"] == {
        "configured": 2, "observed": 2, "ids": ["api1", "api2"],
    }
    assert all(tiles[key]["source"] == "evals/runs/load-20261103-120000.json"
               for key in ("load-w1-p95", "load-errors", "load-replicas"))


def test_ops_marks_demo_stub_mode_and_latency_source(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_STUB", "1")
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    monkeypatch.setenv("LLM_STUB_LATENCY_MS", "")

    tiles = {tile["id"]: tile for tile in build_ops_summary(build_test_deps(), tmp_path)["tiles"]}

    assert tiles["model-mode"]["value"] == "load-test stub"
    assert tiles["stub-latency-source"]["value"]["workflows"]["w1"]["run"] == (
        "w1-20261103-071432-graph.json"
    )


def test_ops_page_formats_load_run_and_endpoint_p95():
    page = (Path(__file__).resolve().parents[1] / "static" / "ops.html").read_text(
        encoding="utf-8",
    )
    assert 'tile.id === "latest-load-test"' in page
    assert 'tile.id.startsWith("load-endpoint-")' in page
    assert "value.run_id" in page and "value.p95_ms" in page

from evals.run_w1 import _served_by, score as score_w1
from evals.run_w2 import score as score_w2


def test_w1_case_serving_uses_the_model_step_that_drives_its_route():
    selected = [
        {"purpose": "w1.red_flag_net", "served_by": "primary"},
        {"purpose": "w1.match", "served_by": "hedge"},
    ]
    assert _served_by(selected, selected, "w1.match") == "hedge"


def test_w1_metrics_include_serving_and_degradation_rates():
    cases = [
        {"expect": {"red_flag": True, "route": "emergency_desk"}, "tags": [], "must_not": []},
        {"expect": {"red_flag": False, "route": "book", "specialty": "SP07",
                    "accept_specialties": ["SP07"]}, "tags": [], "must_not": []},
    ]
    preds = [
        {"red_flag": True, "route": "emergency_desk", "served_by": "none", "degraded": False,
         "latency_s": 0.1},
        {"red_flag": False, "route": "front_desk", "specialty": None, "offered_slots": 0,
         "text_shown": "", "served_by": "hedge", "degraded": True, "latency_s": 0.1},
    ]
    metrics = score_w1(cases, preds, rules_only=False)
    assert metrics["fallback_rate"] == 0.5
    assert metrics["degraded_rate"] == 0.5


def test_w2_metrics_include_serving_and_degradation_rates():
    cases = [{"expect": {"route": "answer", "accept_routes": ["answer"]}, "tags": []}]
    preds = [{
        "route": "answer_degraded", "reply": "Report text\n\nSources: report",
        "verified": False, "cited_docs": ["doc"], "cited_pages": [1], "leak": False,
        "latency_s": 0.5, "served_by": "fallback", "degraded": True,
    }]
    metrics = score_w2(cases, preds)
    assert metrics["fallback_rate"] == 1
    assert metrics["degraded_rate"] == 1

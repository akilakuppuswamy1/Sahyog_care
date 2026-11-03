import json
import hashlib
from pathlib import Path

import pytest

from evals.judge import FaithfulnessJudge, PROMPT_VERSION, faithfulness_status
from services.agent.llm import ModelError
from tools.calibrate_judge import agreement_and_kappa, calibrate
from tools.build_eval_report import build_report

ROOT = Path(__file__).resolve().parents[2]


def test_pro_sets_preserve_original_cases_and_size():
    baselines = {"w1_triage.jsonl": (34, "c3d5a0c463931fe2d4642cd6867bcb999ab5f16c2cab7887acbbc42eb2dcf2c7"),
                 "w2_records.jsonl": (30, "5c2ba814c5093661a736c2c0e23fc76a12d4b8f4761dcd86ec6a237cdbaa3a65")}
    for filename, (count, digest) in baselines.items():
        path = f"evals/sets/{filename}"
        current = (ROOT / path).read_bytes()
        assert hashlib.sha256(b"\n".join(current.splitlines()[:count])).hexdigest() == digest
        cases = [json.loads(x) for x in current.decode().splitlines() if x]
        assert len(cases) == 80
        assert len({c["id"] for c in cases}) == 80
        additions = cases[count:]
        assert all(c["source"] and c["tags"] and c["added_by"] ==
                   "agent draft — clinical review pending" for c in additions)
        if filename.startswith("w1"):
            positives = sum(c["expect"]["red_flag"] for c in cases)
            assert 30 <= positives <= 40
            assert {"negation_trap", "two_specialty", "records_in_w1", "prompt_injection"} <= {
                t for c in additions for t in c["tags"]}
        else:
            patients = {f"PT-W2-{i:04}" for i in range(1, 6)}
            assert all(c["subject_id"] in patients and c["actor_id"] in patients for c in cases)


def test_calibration_set_shape():
    items = [json.loads(x) for x in (ROOT / "evals/sets/judge_calibration.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(items) == len({c["id"] for c in items}) == 30
    assert all(c["human_label"] == "" and c["question"] and c["excerpts"] and c["answer"] for c in items)
    assert {"wrong_number", "wrong_unit", "unsupported_claim", "diagnosis_wording", "another_report"} <= {
        t for c in items for t in c["tags"]}


def test_kappa_known_example():
    result = agreement_and_kappa([1, 1, 1, 1, 0, 0, 0, 0], [1, 1, 1, 0, 1, 0, 0, 0])
    assert result == {"agreement": .75, "kappa": .5}
    assert agreement_and_kappa([1, 1], [1, 1])["kappa"] is None


@pytest.mark.parametrize("label", ["", None, "1", True, 2])
def test_empty_or_invalid_labels_refused_before_call(label):
    class NeverJudge:
        def score(self, *args, **kwargs):
            pytest.fail("Invalid labels must not call any model")
    with pytest.raises(ValueError, match="labels"):
        calibrate([{"human_label": label}], NeverJudge(), "test")


def test_judge_contract_and_independence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_JUDGE", "judge-model")
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL_JUDGE", raising=False)
    class Fake:
        result = {"score": 0, "reason": "The unit is unsupported."}
        def complete_json(self, system, user, **kwargs):
            self.payload = json.loads(user)
            assert kwargs["purpose"] == "w2.faithfulness"
            return self.result
    fake = Fake()
    judge = FaithfulnessJudge(["answer-model"], llm=fake)
    assert judge.score("q", ["e"], "a", trace_id="test")["score"] == 0
    assert fake.payload == {"question": "q", "excerpts": ["e"], "answer": "a"}
    fake.result = {"score": True, "reason": "yes"}
    with pytest.raises(ModelError):
        judge.score("q", [], "a", trace_id="test")
    with pytest.raises(ValueError):
        FaithfulnessJudge(["judge-model:free"], llm=fake)
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL_JUDGE", "answer-model")
    with pytest.raises(ValueError):
        FaithfulnessJudge(["answer-model"], llm=fake)


@pytest.mark.parametrize("kappa,certified", [(.59, False), (.6, True), (None, False)])
def test_report_calibration_gate(tmp_path, kappa, certified):
    meta = {"run_id": "20260101", "judge_model": "judge", "judge_prompt_version": PROMPT_VERSION}
    run = {"meta": meta, "metrics": {"judge_score": .987}}
    (tmp_path / "w2-test.json").write_text(json.dumps(run))
    calibration = {"meta": dict(meta, run_id="20260102"), "metrics": {"kappa": kappa}}
    (tmp_path / "judge-calibration-test.json").write_text(json.dumps(calibration))
    report = build_report(tmp_path)
    assert ("0.987" in report) == certified
    assert ("calibration 20260102" in report) == certified
    if not certified:
        assert "judge not calibrated (kappa" in report
    # A newer failed calibration invalidates the previous successful result.
    newer = {"meta": dict(meta, run_id="20260103"), "metrics": {"kappa": .1}}
    assert "not calibrated" in faithfulness_status(run, [calibration, newer])
    calibration["meta"]["judge_model"] = "different"
    assert "not calibrated" in faithfulness_status(run, [calibration])
    assert faithfulness_status({"metrics": {}}, []) == "not measured"

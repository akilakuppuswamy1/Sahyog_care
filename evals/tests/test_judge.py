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
    assert all(c["question"] and c["excerpts"] and c["answer"] for c in items)
    # The pod has labelled the development set; preserve those labels exactly.
    assert all(type(c["human_label"]) is int for c in items)
    assert [c["human_label"] for c in items] == [
        1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1,
        0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1]
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
    calibration = {"meta": dict(meta, run_id="20260102", calibration_set="holdout"), "metrics": {"kappa": kappa}}
    (tmp_path / "judge-calibration-test.json").write_text(json.dumps(calibration))
    report = build_report(tmp_path)
    assert ("0.987" in report) == certified
    assert ("calibration 20260102" in report) == certified
    if not certified:
        assert "judge not calibrated (kappa" in report
    # A newer failed calibration invalidates the previous successful result.
    newer = {"meta": dict(meta, run_id="20260103", calibration_set="holdout"), "metrics": {"kappa": .1}}
    assert "not calibrated" in faithfulness_status(run, [calibration, newer])
    calibration["meta"]["judge_model"] = "different"
    assert "not calibrated" in faithfulness_status(run, [calibration])
    assert faithfulness_status({"metrics": {}}, []) == "not measured"


def test_holdout_is_exact_pod_labelled_round_two_set():
    original = [json.loads(x) for x in (ROOT / "evals/sets/judge_calibration.jsonl").read_text(encoding="utf-8").splitlines()]
    holdout = [json.loads(x) for x in (ROOT / "evals/sets/judge_calibration_holdout.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(holdout) == len({x["id"] for x in holdout}) == 20
    assert not {x["id"] for x in original} & {x["id"] for x in holdout}
    assert not {(x["question"], x["answer"]) for x in original} & {(x["question"], x["answer"]) for x in holdout}
    assert [x["id"] for x in holdout] == [f"judge-cal-r2-{i:03}" for i in range(1, 21)]
    assert [x["human_label"] for x in holdout] == [1,0,1,1,0,0,1,0,0,1,0,1,0,1,0,1,0,0,0,1]
    assert all(type(x["human_label"]) is int and "tags" not in x for x in holdout)
    assert all(x["labelled_by"] == "pod (human), 2026-11-03, labelled before seeing judge output" for x in holdout)
    key = json.loads((ROOT / "evals/sets/judge_calibration_holdout.key.json").read_text(encoding="utf-8"))
    assert key["usage"] == "Post-hoc category analysis only. Never input to the judge."
    assert set(key["items"]) == {x["id"] for x in holdout}
    assert all(set(item) == {"expected_category"} and item["expected_category"] for item in key["items"].values())


def test_holdout_empty_labels_fail_before_judge_construction(monkeypatch, tmp_path):
    import tools.calibrate_judge as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    set_dir = tmp_path / "evals/sets"
    set_dir.mkdir(parents=True)
    (set_dir / "judge_calibration_holdout.jsonl").write_text(json.dumps({"human_label": ""}), encoding="utf-8")
    monkeypatch.setattr(cli, "FaithfulnessJudge", lambda *a: pytest.fail("Must not construct a model"))
    monkeypatch.setattr(cli, "answering_models", lambda: pytest.fail("Must not read model settings"))
    with pytest.raises(ValueError, match="labels"):
        cli.main(["--set", "holdout", "--runs-dir", str(tmp_path)])
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("selection", ["holdout", "original", "custom"])
def test_set_flag_saves_provenance_and_ignores_analysis_key(monkeypatch, tmp_path, selection):
    import tools.calibrate_judge as cli
    dotenv_calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda path, **kw: dotenv_calls.append((path, kw)))
    set_dir = tmp_path / "evals/sets"
    set_dir.mkdir(parents=True)
    path = set_dir / {"holdout": "judge_calibration_holdout.jsonl", "original": "judge_calibration.jsonl", "custom": "custom.jsonl"}[selection]
    items = [dict(id="a", human_label=1, question="q", excerpts=[{"citation": "E1", "text": "x"}], answer="x"),
             dict(id="b", human_label=0, question="q", excerpts=[{"citation": "E1", "text": "x"}], answer="y")]
    path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")
    key_path = set_dir / "judge_calibration_holdout.key.json"
    key_path.write_text("INVALID JSON: must never be opened", encoding="utf-8")
    original_read = Path.read_text
    def guarded_read(path, *args, **kwargs):
        assert path != key_path, "Analysis key leaked into calibration"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded_read)
    class Fake:
        model = "offline-fake"
        class llm:
            class usage:
                call_records = []
        def score(self, question, excerpts, answer, **kwargs):
            assert question == "q" and excerpts == [{"citation": "E1", "text": "x"}]
            return {"score": int(answer == "x"), "reason": "Offline fixture"}
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "answering_models", lambda: [])
    monkeypatch.setattr(cli, "FaithfulnessJudge", lambda *args: Fake())
    runs = tmp_path / "runs"
    cli.main(["--set", str(path) if selection == "custom" else selection, "--runs-dir", str(runs)])
    result = json.loads(next(runs.glob("*.json")).read_text())
    assert result["meta"]["calibration_set"] == selection
    assert result["meta"]["judge_prompt_version"] == PROMPT_VERSION
    assert result["metrics"] == {"agreement": 1, "kappa": 1}
    assert dotenv_calls == [(tmp_path / ".env", {"override": False})]
    with pytest.raises(ValueError, match="never an analysis key"):
        cli.resolve_set(key_path)


def test_original_kappa_cannot_certify_and_report_shows_both_prompts(tmp_path):
    run = {"meta": {"run_id": "10", "judge_model": "judge", "judge_prompt_version": PROMPT_VERSION},
           "metrics": {"judge_score": .987}}
    original = {"meta": {"run_id": "30", "judge_model": "judge", "judge_prompt_version": "w2-faithfulness-v1", "cases": 30},
                "metrics": {"kappa": .99}}
    assert "not calibrated" in faithfulness_status(run, [original])
    holdout = {"meta": dict(run["meta"], run_id="20", calibration_set="holdout", cases=20), "metrics": {"kappa": .59}}
    assert "not calibrated" in faithfulness_status(run, [original, holdout])
    holdout["metrics"]["kappa"] = .6
    assert "0.987" in faithfulness_status(run, [original, holdout])
    for name, data in [("w2-test.json", run), ("judge-calibration-original.json", original), ("judge-calibration-holdout.json", holdout)]:
        (tmp_path / name).write_text(json.dumps(data))
    report = build_report(tmp_path)
    assert "| original | 30 | 30 | 0.99 | w2-faithfulness-v1 |" in report
    assert f"| holdout | 20 | 20 | 0.6 | {PROMPT_VERSION} |" in report
    holdout["meta"]["judge_prompt_version"] = "old-prompt"
    assert "not calibrated" in faithfulness_status(run, [holdout])


def test_gate_requires_current_prompt_even_when_old_versions_match():
    run = {"meta": {"judge_model": "judge", "judge_prompt_version": "w2-faithfulness-v1"},
           "metrics": {"judge_score": 1}}
    old = {"meta": dict(run["meta"], run_id="99", calibration_set="holdout"), "metrics": {"kappa": 1}}
    assert "not calibrated" in faithfulness_status(run, [old])
    run["meta"]["judge_prompt_version"] = PROMPT_VERSION
    current = {"meta": dict(run["meta"], run_id="01", calibration_set="holdout"), "metrics": {"kappa": .6}}
    assert "calibration 01" in faithfulness_status(run, [current, old])


def test_calibration_never_sends_human_labels_to_judge(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_JUDGE", "judge-model")
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL_JUDGE", raising=False)
    class Fake:
        def complete_json(self, system, user, **kwargs):
            assert json.loads(user) == {"question": "q", "excerpts": ["e"], "answer": "a"}
            assert "human_label" not in system + user
            assert "labelled_by" not in system + user
            return {"score": 1, "reason": "Supported"}
    item = dict(id="test", question="q", excerpts=["e"], answer="a", human_label=0,
                labelled_by="pod (human)")
    result = calibrate([item], FaithfulnessJudge(["answer-model"], llm=Fake()), "test")
    assert result["predictions"][0]["human_label"] == 0


def test_set_default_is_original(monkeypatch, tmp_path):
    import tools.calibrate_judge as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    observed = []
    def resolve(value):
        observed.append(value)
        raise ValueError("selection inspected")
    monkeypatch.setattr(cli, "resolve_set", resolve)
    with pytest.raises(ValueError, match="selection inspected"):
        cli.main([])
    assert observed == ["original"]

"""Explicit model-calling calibration command; empty pod labels fail before any call."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.judge import FaithfulnessJudge, PROMPT_VERSION, answering_models

ROOT = Path(__file__).resolve().parents[1]


def agreement_and_kappa(human, predicted):
    if not human or len(human) != len(predicted):
        raise ValueError("Equal, nonempty label lists are required")
    if any(type(x) is not int or x not in (0, 1) for x in [*human, *predicted]):
        raise ValueError("All human labels must be filled with integer 0 or 1")
    n = len(human)
    agreement = sum(a == b for a, b in zip(human, predicted)) / n
    expected = sum(human.count(v) * predicted.count(v) for v in (0, 1)) / n**2
    # Undefined when both raters assign only one identical class; never certify it.
    return {"agreement": agreement, "kappa": (agreement - expected) / (1 - expected) if expected < 1 else None}


def calibrate(items, judge, run_id):
    labels = [item.get("human_label") for item in items]
    agreement_and_kappa(labels, labels)  # Refuse missing/invalid labels before model calls.
    predictions = [dict(id=item["id"], human_label=item["human_label"], **judge.score(item["question"], item["excerpts"], item["answer"],
                   trace_id=f"calibration-{run_id}-{item['id']}")) for item in items]
    return {"meta": {"run_id": run_id, "judge_model": judge.model, "judge_prompt_version": PROMPT_VERSION},
            "metrics": agreement_and_kappa(labels, [p["score"] for p in predictions]),
            "predictions": predictions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=ROOT / "evals/sets/judge_calibration.jsonl")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "evals/runs")
    args = parser.parse_args()
    items = [json.loads(line) for line in args.set.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = [item.get("human_label") for item in items]
    agreement_and_kappa(labels, labels)
    judge = FaithfulnessJudge(answering_models())
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    result = calibrate(items, judge, run_id)
    result["meta"]["set_sha256"] = hashlib.sha256(args.set.read_bytes()).hexdigest()
    result["meta"]["cases"] = len(items)
    result["meta"]["model_calls"] = judge.llm.usage.call_records
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    path = args.runs_dir / f"judge-calibration-{run_id}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"]))
    print(path)


if __name__ == "__main__":
    main()

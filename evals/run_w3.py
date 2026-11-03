"""Run the W3 scribe eval set through the real graph and OpenRouter.

Usage: python -m evals.run_w3
Writes evals/runs/w3-<timestamp>.json and .md. This command makes model calls.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from services.agent.llm import OpenRouterLLM
from services.agent.w3.formulary import Formulary, default_formulary
from services.agent.w3.graph import compile_w3
from services.agent.w3.nodes import GROUNDING_VERSION, PROMPT_VERSION
from services.agent.w3.stock import ClinicCsvStockAdapter
from services.agent.w3.store import InMemoryW3Store
from services.common.deid import warm_analyzer

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "evals" / "sets" / "w3_scribe.jsonl"
RUNS = ROOT / "evals" / "runs"
TARGETS = {
    "latency_p95_s": (20, "<"),
    "invented_molecules": (0, "=="),
    "field_f1": (0.9, ">="),
    "medicine_precision": (0.9, ">="),
    "medicine_recall": (0.9, ">="),
}
SOAP_FIELDS = ("S", "O", "A", "P")


def _tokens(text: str) -> Counter:
    return Counter(re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE))


def _f1(reference: str, predicted: str) -> tuple[float, float, float]:
    expected, actual = _tokens(reference), _tokens(predicted)
    if not expected and not actual:
        return 1.0, 1.0, 1.0
    overlap = sum((expected & actual).values())
    precision = overlap / sum(actual.values()) if actual else 0.0
    recall = overlap / sum(expected.values()) if expected else 0.0
    score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, score


def _key(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


PLACEHOLDER_KEY = _key("[doctor to specify]")


def _value(value: object) -> str:
    """Normalised comparison key; the doctor-to-specify sentinel counts as empty."""
    key = _key(value)
    return "" if key == PLACEHOLDER_KEY else key


def _medicine_key(medicine: dict) -> tuple[str, ...]:
    molecule, dose = _value(medicine.get("molecule", "")), _value(medicine.get("dose", ""))
    if molecule:
        return ("molecule", molecule, dose)
    return ("name", _key(medicine.get("name", "")), dose)


def _supported(medicine: dict, transcript: str, formulary: Formulary) -> bool:
    """A molecule is supported if the transcript states it or the stated brand maps to it uniquely."""
    molecule = _value(medicine.get("molecule", ""))
    if f" {molecule} " in f" {_key(transcript)} " and not formulary.is_brand_only(molecule):
        return True
    hit = formulary.lookup(str(medicine.get("name", "")))
    return hit.status == "one" and _key(hit.molecule) == molecule


def stock_comparisons(case: dict, prediction: dict, stock) -> list[dict]:
    """Expected stock (the stock source applied to the gold medicine) against what the draft showed."""
    clinic_id = case.get("clinic_id", prediction["clinic_id"])
    rows = []
    for gold in case["gold_medicines"]:
        matching = next((item for item in prediction["medicines"]
                         if _key(item.get("name", "")) == _key(gold.get("name", ""))), None)
        rows.append({
            "name": gold["name"], "molecule": gold.get("molecule", ""),
            "expected": stock.check(clinic_id, gold)["status"],
            "actual": matching.get("status") if matching else None,
        })
    return rows


def _model_call_details(records: list[dict]) -> dict:
    selected = next((record for record in records if record.get("selected")), None)
    return {
        "attempts": len(records),
        "served_by": selected.get("served_by") if selected else None,
        "provider": selected.get("provider") if selected else None,
        "latency_s": selected.get("latency_s") if selected else None,
        "calls": [{
            "served_by": record.get("served_by"),
            "provider": record.get("provider"),
            "latency_s": record.get("latency_s"),
            "success": record.get("success", False),
            "selected": record.get("selected", False),
        } for record in records],
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    low, high = int(position), min(len(ordered) - 1, int(position) + 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def score(cases: list[dict], predictions: list[dict], stock: ClinicCsvStockAdapter,
          formulary: Formulary | None = None) -> dict:
    formulary = formulary or default_formulary()
    field_values = {field: [] for field in SOAP_FIELDS}
    medicine_tp = medicine_predicted = medicine_expected = 0
    invented_molecules = 0
    class_only_total = class_only_handled = 0
    stock_total = stock_correct = 0

    for case, prediction in zip(cases, predictions):
        gold_soap = case["gold_soap"]
        predicted_soap = prediction["soap"]
        for field in SOAP_FIELDS:
            field_values[field].append(_f1(gold_soap[field], predicted_soap.get(field, ""))[2])

        gold_medicines = case["gold_medicines"]
        predicted_medicines = prediction["medicines"]
        gold_keys = Counter(_medicine_key(item) for item in gold_medicines)
        predicted_keys = Counter(_medicine_key(item) for item in predicted_medicines)
        medicine_tp += sum((gold_keys & predicted_keys).values())
        medicine_expected += sum(gold_keys.values())
        medicine_predicted += sum(predicted_keys.values())

        for medicine in predicted_medicines:
            if _value(medicine.get("molecule", "")) and not _supported(medicine, case["transcript"], formulary):
                invented_molecules += 1

        if "class_only" in case["tags"]:
            class_only_total += 1
            expected_classes = {_key(item["name"]) for item in gold_medicines}
            handled = any(
                _key(item.get("name", "")) in expected_classes
                and item.get("molecule") in ("", "[doctor to specify]")
                for item in predicted_medicines
            )
            class_only_handled += int(handled)

        for row in stock_comparisons(case, prediction, stock):
            stock_total += 1
            stock_correct += int(row["actual"] == row["expected"])

    latencies = [float(item["latency_s"]) for item in predictions]
    field_f1 = {field: round(sum(values) / len(values), 3) if values else None
                for field, values in field_values.items()}
    return {
        "cases": len(cases),
        "field_f1": field_f1,
        "field_f1_macro": round(sum(field_f1.values()) / len(field_f1), 3),
        "medicine_precision": round(medicine_tp / medicine_predicted, 3) if medicine_predicted else 0.0,
        "medicine_recall": round(medicine_tp / medicine_expected, 3) if medicine_expected else 1.0,
        "invented_molecules": invented_molecules,
        "class_only_handled": {
            "correct": class_only_handled, "total": class_only_total,
            "rate": round(class_only_handled / class_only_total, 3) if class_only_total else None,
        },
        "stock_flag_correct": {
            "correct": stock_correct, "total": stock_total,
            "rate": round(stock_correct / stock_total, 3) if stock_total else None,
        },
        "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_s": _percentile(latencies, 0.95),
        "review_time_p50_s": None,
        "review_time_p95_s": None,
    }


def report_md(meta: dict, metrics: dict, predictions: list[dict]) -> str:
    lines = [
        f"# W3 scribe eval run {meta['run_id']}", "",
        f"- model: {meta['model']} · prompt: {meta['prompt_version']} · grounding: {meta['grounding_version']} · "
        f"set: v{meta['set_version']} · workflow: w3 · tokens: {meta['tokens']}", "",
        "| Metric | Measured | Target | Met |", "|---|---:|---:|---|",
    ]
    for field in SOAP_FIELDS:
        lines.append(f"| {field} field F1 | {metrics['field_f1'][field]} | ≥ 0.9 | "
                     f"{'yes' if metrics['field_f1'][field] is not None and metrics['field_f1'][field] >= .9 else '**NO**'} |")
    for key, target in TARGETS.items():
        value = metrics["field_f1_macro"] if key == "field_f1" else metrics.get(key)
        condition = value is not None and (value == target[0] if target[1] == "==" else
                                           value >= target[0] if target[1] == ">=" else value < target[0])
        lines.append(f"| {key} | {value if value is not None else 'not measured'} | "
                     f"{target[1]} {target[0]} | {'yes' if condition else '**NO**'} |")
    lines += [
        f"| class_only_handled | {metrics['class_only_handled']} | all tagged cases | "
        f"{'yes' if metrics['class_only_handled']['rate'] == 1 else '**NO**'} |",
        f"| stock_flag_correct | {metrics['stock_flag_correct']} | all grounded medicines | "
        f"{'yes' if metrics['stock_flag_correct']['rate'] == 1 else '**NO**'} |",
        f"| review time p50 / p95 (s) | not measured | < 90 s | — |",
        "", "## Case latency", "", "| Case | Route | Latency (s) |", "|---|---|---:|",
    ]
    lines.extend(f"| {prediction['id']} | {prediction['route']} | {prediction['latency_s']} |"
                 for prediction in predictions)
    return "\n".join(lines) + "\n"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    warm_analyzer()
    cases = [json.loads(line) for line in SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    stock_dir = Path(os.environ.get(
        "W3_STOCK_DIR", ROOT / "data" / "synthetic" / "out" / "w3" / "stock"
    ))
    stock = ClinicCsvStockAdapter(stock_dir)
    formulary = default_formulary()
    clinic_ids = sorted(path.stem for path in stock.paths)
    if not clinic_ids:
        raise SystemExit(f"No clinic stock files found in {stock_dir}")
    default_clinic = clinic_ids[0]
    encounters = {
        f"EVAL-{index:03d}": {
            "encounter_id": f"EVAL-{index:03d}", "patient_id": f"PT-W3-EVAL-{index:03d}",
            "doctor_id": "DR-W3-EVAL", "clinic_id": case.get("clinic_id", default_clinic),
        }
        for index, case in enumerate(cases, start=1)
    }
    store = InMemoryW3Store(encounters)
    llm = OpenRouterLLM(workflow="w3")
    graph = compile_w3(llm, store, stock, InMemorySaver(), formulary=formulary)
    predictions = []
    for index, case in enumerate(cases, start=1):
        encounter_id = f"EVAL-{index:03d}"
        trace_id = f"w3-eval-{case['id']}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        out = graph.invoke(
            {"transcript": case["transcript"], "encounter_id": encounter_id,
             "doctor_id": "DR-W3-EVAL", "trace_id": trace_id},
            {"configurable": {"thread_id": trace_id}},
        )
        with llm.usage._lock:
            call_records = [dict(record) for record in llm.usage.call_records
                            if record["trace_id"] == trace_id]
        state = out
        soap = state.get("soap_json") or {**dict.fromkeys(SOAP_FIELDS, ""), "medicines": []}
        predictions.append({
            "id": case["id"], "route": state.get("route", "needs_review"),
            "soap": {field: soap.get(field, "") for field in SOAP_FIELDS},
            "medicines": soap.get("medicines", []), "flags": state.get("flags", []),
            "initial_model": state.get("initial_soap_json"),
            "clinic_id": encounters[encounter_id]["clinic_id"],
            "latency_s": round(time.perf_counter() - started, 3),
            "model_calls": _model_call_details(call_records),
        })
        predictions[-1]["stock_expected_vs_actual"] = stock_comparisons(case, predictions[-1], stock)
        print(f"{case['id']}: {predictions[-1]['route']:15} {predictions[-1]['latency_s']}s")

    metrics = score(cases, predictions, stock, formulary)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {
        "run_id": run_id, "mode": "graph", "workflow": "w3", "model": llm.model,
        "prompt_version": PROMPT_VERSION, "grounding_version": GROUNDING_VERSION,
        "set_version": cases[0].get("set_version") if cases else None,
        "tokens": {"calls": llm.usage.calls, "prompt": llm.usage.prompt_tokens,
                   "completion": llm.usage.completion_tokens},
    }
    result = {"meta": meta, "metrics": metrics, "predictions": predictions}
    RUNS.mkdir(parents=True, exist_ok=True)
    stem = RUNS / f"w3-{run_id}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    stem.with_suffix(".md").write_text(report_md(meta, metrics, predictions), encoding="utf-8")
    print(f"\nWrote {stem.name}.json and .md")


if __name__ == "__main__":
    main()

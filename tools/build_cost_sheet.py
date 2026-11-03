"""Build docs/cost-sheet.md from saved evaluation JSON only; never runs an evaluation."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _latest_runs(runs_dir: Path) -> dict[str, tuple[Path, dict]]:
    latest: dict[str, tuple[Path, dict]] = {}
    for path in sorted(runs_dir.glob("*.json")):
        workflow = path.name.split("-", 1)[0]
        if workflow not in ("w1", "w2", "w3"):
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("meta", {}).get("mode") == "rules":
            continue
        previous = latest.get(workflow)
        run_id = run.get("meta", {}).get("run_id", path.stem)
        previous_id = previous[1].get("meta", {}).get("run_id", previous[0].stem) if previous else None
        if previous is None or run_id > previous_id:
            latest[workflow] = (path, run)
    return latest


def _price(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _run_values(workflow: str, pair: tuple[Path, dict] | None) -> dict | None:
    if pair is None:
        return None
    path, run = pair
    tokens = run.get("meta", {}).get("tokens", {})
    if not all(isinstance(tokens.get(key), (int, float)) and tokens[key] >= 0
               for key in ("calls", "prompt", "completion")):
        return None
    if workflow == "w1":
        units = run.get("metrics", {}).get("cases")
        unit_name = "triage turns"
    elif workflow == "w3":
        units = run.get("metrics", {}).get("cases")
        unit_name = "scribe drafts"
    else:
        units = sum(prediction.get("route") in {"answer", "answer_degraded"}
                    for prediction in run.get("predictions", []))
        unit_name = "record answers"
    if not isinstance(units, (int, float)) or units <= 0:
        return None
    meta = run.get("meta", {})
    return {
        "run_id": meta.get("run_id", path.stem),
        "path": path.name,
        "model": meta.get("model", "not recorded"),
        "calls": tokens["calls"],
        "tokens_in": tokens["prompt"],
        "tokens_out": tokens["completion"],
        "units": units,
        "unit_name": unit_name,
    }


def build_cost_sheet(runs_dir: Path) -> str:
    latest = _latest_runs(runs_dir)
    w1 = _run_values("w1", latest.get("w1"))
    w2 = _run_values("w2", latest.get("w2"))
    w3 = _run_values("w3", latest.get("w3"))
    p_in = {workflow: _price(f"PRICE_IN_PER_MTOK_{workflow.upper()}")
            for workflow in ("w1", "w2", "w3")}
    p_out = {workflow: _price(f"PRICE_OUT_PER_MTOK_{workflow.upper()}")
             for workflow in ("w1", "w2", "w3")}
    values = {"w1": w1, "w2": w2, "w3": w3}
    unit_costs: dict[str, float] = {}
    lines = [
        "# Cost worksheet (Pro, deck p.34)",
        "",
        "Generated from the latest saved graph-evaluation JSON files. No eval or model call is run.",
        "",
        f"Price list date: {os.environ.get('PRICE_DATE') or 'to fill'}",
        "",
        "Prices are read only from `PRICE_IN_PER_MTOK_W1`, `PRICE_OUT_PER_MTOK_W1`, "
        "`PRICE_IN_PER_MTOK_W2`, `PRICE_OUT_PER_MTOK_W2`, `PRICE_IN_PER_MTOK_W3`, and "
        "`PRICE_OUT_PER_MTOK_W3`; figures are in the provider's price currency.",
        "Unset prices remain formulas marked **to fill**; no price is embedded in this worksheet.",
        "",
        "| Line | Measured source and run id | Model | Calls | Measured input / output tokens | Unit count | Cost formula / measured unit cost |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for workflow, label in (("w1", "Triage"), ("w2", "Record answers"), ("w3", "Scribe")):
        run = values[workflow]
        if run is None:
            lines.append(f"| {label} | not measured (saved run JSON with token totals and denominator required) | "
                         "not recorded | to fill | to fill | to fill | formula to fill |")
            continue
        price_in, price_out = p_in[workflow], p_out[workflow]
        input_per_unit = run["tokens_in"] / run["units"]
        output_per_unit = run["tokens_out"] / run["units"]
        formula = (f"({run['units']} {run['unit_name']} × (({input_per_unit:.2f} input tokens × "
                   f"PRICE_IN_PER_MTOK_{workflow.upper()}) + ({output_per_unit:.2f} output tokens × "
                   f"PRICE_OUT_PER_MTOK_{workflow.upper()})) / 1,000,000) / {run['units']} "
                   f"{run['unit_name']}")
        if price_in is not None and price_out is not None:
            total = (run["tokens_in"] * price_in + run["tokens_out"] * price_out) / 1_000_000
            unit_costs[workflow] = total / run["units"]
            formula += f" = {unit_costs[workflow]:.8f}"
        else:
            formula += " = to fill"
        lines.append(
            f"| {label} | [evals/runs/{run['path']}](../evals/runs/{run['path']}) "
            f"({run['run_id']}) | {run['model']} | {run['calls']} | "
            f"{run['tokens_in']} / {run['tokens_out']} | {run['units']} {run['unit_name']} | {formula} |"
        )
    lines += [
        "| Speech-to-text | — | not built | — | not measured | — | not built |",
        "| Embeddings | Local BAAI/bge-m3 | local model | — | not applicable | — | no API cost; local compute/storage excluded |",
        "",
        "## Cost per consultation",
        "",
        "Formula: **cost per consultation = total cost / consultations**. Consultation count and the W1/W2 "
        "call mix are not linked by the current eval runs, so an actual per-consultation figure is **to fill**.",
        "For a clearly labelled scenario only, assume one measured W1 triage turn and one measured W2 record "
        "answer per modeled consultation:",
    ]
    if w1 and w2 and "w1" in unit_costs and "w2" in unit_costs:
        scenario = unit_costs["w1"] + unit_costs["w2"]
        lines.append(f"- Total scenario cost = W1 unit cost + W2 unit cost = {scenario:.8f}; "
                     f"modeled consultations = 1; scenario cost per consultation = total / 1 = {scenario:.8f}.")
    else:
        lines.append("- Total scenario cost = W1 triage unit cost + W2 record-answer unit cost; "
                     "modeled consultations = 1; cost per consultation = total / 1 = to fill.")
    if w1 and w2:
        lines.append(f"- Model routing: W2 uses `{w2['model']}` (run {w2['run_id']}); "
                     f"W1 uses `{w1['model']}` (run {w1['run_id']}). W2 is routed to the smaller model.")
    else:
        lines.append("- Model routing: W2 is routed to the smaller model; saved run ids/models are to fill.")
    if w3:
        lines.append(f"- Scribe model: `{w3['model']}` (run {w3['run_id']}); W3 cost is measured per scribe draft.")
    lines += [
        "",
        "Token totals are run-level measured values divided by the recorded evaluation denominator; they are "
        "not isolated per-call token measurements. W2 totals may include model calls for multiple purposes, "
        "so its per-answer figure is an amortized run average.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "evals" / "runs")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "cost-sheet.md")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_cost_sheet(args.runs_dir), encoding="utf-8")


if __name__ == "__main__":
    main()

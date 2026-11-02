"""Run the W1 eval set through the real graph and report the numbers the demo screen needs (deck p.16, p.47).

Usage (from the repo root, with .env holding OPENROUTER_API_KEY and OPENROUTER_MODEL):
    python -m evals.run_w1                 # full graph with the model
    python -m evals.run_w1 --rules-only    # red-flag rules only, no model, no cost

Writes evals/runs/w1-<timestamp>.json and .md. A target that is missed is reported with the number
reached, never dropped (deck p.16).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.agent.store import InMemoryStore
from services.agent.w1 import redflags
from services.agent.w1.guards import DIAGNOSIS

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "evals/sets/w1_triage.jsonl"
RUNS = ROOT / "evals/runs"


class EvalStore(InMemoryStore):
    """Returns three synthetic candidates for any specialty, so the eval measures routing, not the calendar."""
    def shortlist(self, specialty_ids, language, n=3):
        t0 = datetime(2026, 11, 16, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return [dict(doctor_id=f"EV{i}", doctor_name=f"Dr. Eval {i}", languages=["en", "hi"], specialty_id=specialty_ids[0],
                     specialty=specialty_ids[0], clinic_id="PUN-H01", clinic="Eval clinic", city="Pune",
                     slot_id=f"EV{i}-{specialty_ids[0]}", starts_at=(t0 + timedelta(hours=i)).isoformat())
                for i in range(n)] if specialty_ids else []


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def predict_graph(graph, case) -> dict:
    cfg = {"configurable": {"thread_id": f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"}}
    t = time.perf_counter()
    out = graph.invoke({"text": case["input"], "age": case["age"], "patient_id": "PT-EVAL"}, cfg)
    latency = time.perf_counter() - t
    intr = out.get("__interrupt__") or []
    pause = intr[0].value if intr else None
    if out.get("red_flags"):
        route = out.get("route") or "emergency_desk"
    elif pause and pause["type"] == "clarify":
        route = "clarify"
    elif pause and pause["type"] == "pick_slot":
        route = "book"
    else:
        route = out.get("route") or "unknown"
    text_shown = " ".join(filter(None, [out.get("reply"), (pause or {}).get("question")]))
    return dict(red_flag=bool(out.get("red_flags")), flags=out.get("red_flags", []), route=route,
                specialty=out.get("specialty") if route == "book" else None, alternative=out.get("alternative"),
                confidence=out.get("confidence"), latency_s=round(latency, 3),
                offered_slots=len((pause or {}).get("options", [])), text_shown=text_shown)


def predict_rules(case) -> dict:
    r = redflags.evaluate(case["input"], case["age"])
    return dict(red_flag=r.red_flag, flags=list(r.fired), route=r.route or "not_evaluated", specialty=None,
                latency_s=0.0, offered_slots=0, text_shown="")


def score(cases, preds, rules_only: bool) -> dict:
    pos = [(c, p) for c, p in zip(cases, preds) if c["expect"]["red_flag"]]
    neg = [(c, p) for c, p in zip(cases, preds) if not c["expect"]["red_flag"]]
    m = {"cases": len(cases),
         "red_flag_recall": sum(p["red_flag"] for _, p in pos) / len(pos),
         "red_flag_false_alarms": sum(p["red_flag"] for _, p in neg),
         "red_flag_route_correct": sum(p["route"] == c["expect"]["route"] for c, p in pos) / len(pos)}
    if rules_only:
        return m
    routine = [(c, p) for c, p in neg if c["expect"]["route"] == "book"]
    clar = [(c, p) for c, p in neg if c["expect"]["route"] == "clarify"]
    m["specialty_accuracy"] = sum(p["specialty"] in c["expect"]["accept_specialties"] for c, p in routine) / len(routine)
    m["specialty_exact_top1"] = sum(p["specialty"] == c["expect"]["specialty"] for c, p in routine) / len(routine)
    redirects = [(c, p) for c, p in neg if c["expect"]["route"] == "redirect_records"]
    m["redirect_records_correct"] = sum(p["route"] == "redirect_records" for _, p in redirects) / len(redirects) if redirects else None
    m["clarify_correct"] = sum(p["route"] == "clarify" for _, p in clar) / len(clar) if clar else None
    m["must_not_violations"] = sum(
        (("specialty" in c["must_not"] and p["specialty"]) or ("slot" in c["must_not"] and p["offered_slots"])
         or ("diagnosis" in c["must_not"] and bool(DIAGNOSIS.search(p["text_shown"])))) and 1 or 0
        for c, p in zip(cases, preds))
    lat = sorted(p["latency_s"] for p in preds)
    m["latency_p50_s"] = round(statistics.median(lat), 2)
    m["latency_p95_s"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2)
    by_tag: dict[str, list[bool]] = {}
    for c, p in routine:
        for t in c["tags"]:
            by_tag.setdefault(t, []).append(p["specialty"] in c["expect"]["accept_specialties"])
    m["specialty_accuracy_by_tag"] = {t: round(sum(v) / len(v), 2) for t, v in by_tag.items()}
    return m


TARGETS = {"red_flag_recall": (1.0, ">="), "specialty_accuracy": (0.85, ">="), "must_not_violations": (0, "=="),
           "latency_p95_s": (4.0, "<")}


def report_md(meta, m, cases, preds) -> str:
    lines = [f"# W1 eval run {meta['run_id']}", "",
             f"- mode: {meta['mode']} · model: {meta['model'] or 'none'} · rules: {redflags.RULES_VERSION} · "
             f"prompts: {meta['prompt_version']} · set: {meta['set']} ({m['cases']} cases)",
             f"- tokens: {meta['tokens']}", "", "| Metric | Measured | Target | Met |", "|---|---|---|---|"]
    for k, v in m.items():
        if isinstance(v, dict):
            continue
        tgt = TARGETS.get(k)
        met = ""
        if tgt and v is not None:
            met = "yes" if {">=": v >= tgt[0], "==": v == tgt[0], "<": v < tgt[0]}[tgt[1]] else "**NO**"
        lines.append(f"| {k} | {v} | {(tgt[1] + ' ' + str(tgt[0])) if tgt else ''} | {met} |")
    if m.get("specialty_accuracy_by_tag"):
        lines += ["", "Specialty accuracy by tag: " + ", ".join(f"{k} {v}" for k, v in m["specialty_accuracy_by_tag"].items())]
    lines += ["", "## Failures", "", "| Case | Expected | Got |", "|---|---|---|"]
    for c, p in zip(cases, preds):
        e = c["expect"]
        bad = (p["red_flag"] != e["red_flag"]) or (e["red_flag"] and p["route"] != e["route"]) or \
              (meta["mode"] == "graph" and not e["red_flag"] and (
                  p["route"] != e["route"] or (e["route"] == "book" and p["specialty"] not in e["accept_specialties"])))
        if bad:
            lines.append(f"| {c['id']} | {e['route']} {e.get('specialty', '')} | {p['route']} {p.get('specialty') or ''} {p['flags']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules-only", action="store_true")
    a = ap.parse_args()
    cases = [json.loads(x) for x in SET.read_text(encoding="utf-8").splitlines() if x.strip()]
    meta = {"run_id": datetime.now().strftime("%Y%m%d-%H%M%S"), "mode": "rules" if a.rules_only else "graph",
            "set": SET.name, "model": None, "prompt_version": None, "tokens": None}
    if a.rules_only:
        preds = [predict_rules(c) for c in cases]
    else:
        load_env()
        from langgraph.checkpoint.memory import InMemorySaver
        from services.agent.llm import OpenRouterLLM
        from services.agent.w1.graph import compile_w1
        from services.agent.w1.matcher import PROMPT_VERSION
        llm = OpenRouterLLM(workflow="w1")
        graph = compile_w1(llm, EvalStore(), InMemorySaver())
        preds = []
        for c in cases:
            preds.append(predict_graph(graph, c))
            print(f"{c['id']}: {preds[-1]['route']:15} {preds[-1].get('specialty') or ''} {preds[-1]['latency_s']}s")
        meta.update(model=llm.model, prompt_version=PROMPT_VERSION,
                    tokens={"calls": llm.usage.calls, "prompt": llm.usage.prompt_tokens,
                            "completion": llm.usage.completion_tokens, "by_purpose": llm.usage.by_purpose})
    m = score(cases, preds, a.rules_only)
    RUNS.mkdir(parents=True, exist_ok=True)
    stem = RUNS / f"w1-{meta['run_id']}-{meta['mode']}"
    stem.with_suffix(".json").write_text(json.dumps({"meta": meta, "metrics": m, "predictions": [
        {"id": c["id"], **{k: v for k, v in p.items() if k != "text_shown"}} for c, p in zip(cases, preds)]}, indent=2))
    stem.with_suffix(".md").write_text(report_md(meta, m, cases, preds), encoding="utf-8")
    print(json.dumps(m, indent=2))
    print(f"report: {stem.with_suffix('.md')}")


if __name__ == "__main__":
    main()

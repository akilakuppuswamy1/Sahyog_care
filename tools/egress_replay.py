"""Seeded offline egress replay; never opens a network transport or writes payloads."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from presidio_analyzer import AnalyzerEngine
from services.common.deid import analyzer, prepare_egress, verhoeff_valid

CLEAN = ["B12 500 mcg", "HbA1c 8.1 %", "BP 140/90", "Dr. visit on day 3"]
# Independently maintained detection expressions, not imports from the scrubber.
SCAN = re.compile(r"(?<!\d)\d{10,14}(?!\d)|\+91[ -]?\d{5}[ -]?\d{5}|\b\d{2}-\d{4}-\d{4}-\d{4}\b|\b\d{4}[ -]\d{4}[ -]\d{4}\b|[\w.+-]+@[\w.-]+|\b[A-Z]{5}\d{4}[A-Z]\b|\b[1-9]\d{5}\b|\b(?:PT-|MRN)[\w-]+|\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")


def generate(seed=701, count=200):
    rng = random.Random(seed)
    rows = []
    for i in range(count):
        prefix = str(rng.randrange(20000000000, 99999999999))
        aadhaar = next(prefix + str(c) for c in range(10) if verhoeff_valid(prefix + str(c)))
        identifiers = {
            "PERSON": rng.choice(["Aarav Sharma", "Priya Nair", "Rohan Mehta", "Ananya Rao"]),
            "PHONE": rng.choice(["+91 ", ""]) + str(rng.randrange(6000000000, 9999999999)),
            "AADHAAR": aadhaar,
            "ABHA": str(rng.randrange(10000000000000, 99999999999999)),
            "ABHA_ADDRESS": f"synthetic{i}@" + rng.choice(["abdm", "sbx"]),
            "PAN": "ZZZPS" + str(rng.randrange(1000, 9999)) + "Z",
            "PIN": str(rng.randrange(100000, 899999)),
            "PATIENT_ID": rng.choice([f"PT-SYN-{i:04}", f"MRN-{i:06}"]),
            "EMAIL": f"synthetic{i}@example.invalid",
            "DOB": f"{rng.randrange(1,28):02}/04/1980",
        }
        workflow = ("w1", "w2", "w3")[i % 3]
        clinical = {"w1": "I have a cough for two days.", "w2": "What does my HbA1c result mean?", "w3": "S: cough. O: BP 140/90. P: review on day 3."}[workflow]
        fields = list(identifiers.items()); rng.shuffle(fields)
        text = clinical + " " + "; ".join(("Patient name" if k == "PERSON" else k) + ": " + v for k,v in fields)
        rows.append((workflow, text, identifiers))
    return rows


def enforce_zero_hits(report):
    if report["total_hits"] or report["refused"]:
        raise RuntimeError("ADR-001 trigger c: stop egress until recognisers are fixed")


def replay(seed=701):
    from services.agent.llm import ModelError
    nlp = analyzer().nlp_engine
    independent = AnalyzerEngine(nlp_engine=nlp)
    totals, removed = Counter(), Counter()
    hits = refused = 0
    timings = []
    for workflow, text, identifiers in generate(seed):
        try:
            system, outbound, _mapping, ms = prepare_egress("Return clinical JSON.", text)
        except ModelError:
            refused += 1
            # Failed fixtures count against recall; never persist the raw payload.
            totals.update(identifiers.keys())
            continue
        timings.append(ms)
        combined = system + " " + outbound
        hits += len(SCAN.findall(combined))
        hits += len(independent.analyze(text=combined, language="en", entities=["PERSON", "PHONE_NUMBER"], score_threshold=0.5))
        for kind, value in identifiers.items():
            totals[kind] += 1
            removed[kind] += value not in combined
    false_positive = sum(prepare_egress("", text)[1] != text for text in CLEAN)
    return {"seed": seed, "payloads": 200, "total_hits": hits, "refused": refused,
            "recall": {k: removed[k]/totals[k] for k in totals},
            "false_positive_rate": false_positive/len(CLEAN),
            "deid_ms_median": statistics.median(timings) if timings else None,
            "deid_ms_p95": sorted(timings)[int((len(timings)-1)*.95)] if timings else None,
            "deid_ms_max": max(timings) if timings else None}


def main():
    report = replay()
    run_id = "egress-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report["run_id"] = run_id
    destination = ROOT / "evals/runs"; destination.mkdir(exist_ok=True)
    (destination / (run_id + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    (destination / (run_id + ".md")).write_text(f"# {run_id}\n\nOffline, synthetic only; no network.\n\n```json\n{json.dumps(report, indent=2)}\n```\n")
    print(json.dumps(report))
    enforce_zero_hits(report)


if __name__ == "__main__":
    main()

"""Generate synthetic report PDFs for W2 (deck p.20): the fixed eval cohort plus seeded bulk patients.

Writes, under data/synthetic/out/reports/:
  pdf/<doc_id>.pdf      the rendered reports (text PDFs)
  manifest.jsonl        ground truth per document (patient, type, date, page texts) for evals
  patients.csv          patient master rows (synthetic = true)
  consents.csv          caregiver consents (relative expiry, resolved at load time)

Decision (AI log): report text comes from seeded templates over real test names and reference ranges,
not from an LLM. Values must be exact for eval cases, and templates make that guaranteed. An LLM layer
for free-text radiology prose is a Pro-tier option.

Usage: python data/synthetic/generate_reports.py [--bulk-patients 50] [--seed 20261102]
       (the pack's full 2,000-PDF layer is --bulk-patients 480; start small, embedding runs on CPU)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data.synthetic.report_cohort import CONSENTS, FIXTURE_REPORTS, PATIENTS, lab  # noqa: E402

GENERATOR_VERSION = "reports-v1"
OUT = ROOT / "data" / "synthetic" / "out" / "reports"

# (test, unit, low, high, sd-ish spread) — reference ranges are typical adult values, for synthetic data only
PANELS = {
    "Complete Blood Count": ("COMPLETE BLOOD COUNT", [("Haemoglobin (Hb)", "g/dL", 12.0, 16.0),
                                                      ("Total WBC count", "x10^3/uL", 4.0, 11.0),
                                                      ("Platelet count", "lakh/uL", 1.5, 4.1)]),
    "Liver Function Test": ("LIVER FUNCTION TEST", [("ALT (SGPT)", "U/L", 7, 56), ("AST (SGOT)", "U/L", 8, 48),
                                                    ("Total bilirubin", "mg/dL", 0.1, 1.2)]),
    "Kidney Function Test": ("KIDNEY FUNCTION TEST", [("Serum creatinine", "mg/dL", 0.6, 1.2),
                                                      ("Blood urea", "mg/dL", 15, 40)]),
    "Lipid Profile": ("LIPID PROFILE", [("Total cholesterol", "mg/dL", 120, 200), ("LDL cholesterol", "mg/dL", 50, 100),
                                        ("HDL cholesterol", "mg/dL", 50, 80), ("Triglycerides", "mg/dL", 60, 150)]),
    "HbA1c": ("GLYCATED HAEMOGLOBIN", [("HbA1c", "%", 4.0, 5.7)]),
    "Thyroid Profile": ("THYROID FUNCTION", [("TSH", "uIU/mL", 0.4, 4.0)]),
    "Vitamin B12": ("VITAMIN B12", [("Vitamin B12", "pg/mL", 200, 900)]),
}
RADIOLOGY = {
    "Chest X-ray": (["Lung fields are clear.", "Cardiac size is normal.", "No pleural effusion."],
                    ["No active disease in the chest."]),
    "Ultrasound Abdomen": (["Liver, gall bladder, pancreas, spleen and kidneys appear normal."],
                           ["Normal ultrasound abdomen."]),
}
FIRST = ["Aarav", "Diya", "Kabir", "Isha", "Rohan", "Sneha", "Vikram", "Anjali", "Farhan", "Meera", "Sanjay", "Pooja"]
LAST = ["Patil", "Reddy", "Iyer", "Khan", "Joshi", "Naidu", "Gupta", "Kulkarni", "Rao", "Shah", "Das", "Menon"]


def _value(rng: random.Random, lo: float, hi: float) -> tuple[str, str]:
    r = rng.random()
    span = hi - lo
    v = lo - span * rng.uniform(0.05, 0.3) if r < 0.15 else hi + span * rng.uniform(0.05, 0.6) if r > 0.8 \
        else rng.uniform(lo, hi)
    flag = "LOW" if v < lo else "HIGH" if v > hi else ""
    fmt = "{:.1f}" if span < 20 else "{:.0f}"
    return fmt.format(v), flag


def bulk(n: int, seed: int) -> tuple[list[tuple], list[dict]]:
    rng = random.Random(seed)
    patients, reports = [], []
    for i in range(1, n + 1):
        pid = f"PT-BLK-{i:04d}"
        dob = date(1950, 1, 1) + timedelta(days=rng.randint(0, 365 * 55))
        patients.append((pid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", dob.isoformat(),
                         f"9{rng.randint(100000000, 999999999)}", rng.choice(["en", "hi", "mr", "te"])))
        for j in range(rng.randint(3, 5)):
            d = (date(2026, 10, 31) - timedelta(days=rng.randint(0, 300))).isoformat()
            if rng.random() < 0.8:
                title = rng.choice(list(PANELS))
                header, tests = PANELS[title]
                rows = []
                for t, u, lo, hi in tests:
                    v, flag = _value(rng, lo, hi)          # one draw: the value and its flag must agree
                    rows.append((t, v, u, f"{lo}-{hi}", flag))
                pages, rtype = [lab(header, rows)], "lab"
            else:
                title = rng.choice(list(RADIOLOGY))
                findings, impression = RADIOLOGY[title]
                pages, rtype = [[("FINDINGS", findings), ("IMPRESSION", impression)]], "radiology"
            reports.append({"doc_id": f"DOC-BLK-{i:04d}-{j}", "patient_id": pid, "type": rtype, "date": d,
                            "title": title, "pages": pages})
    return patients, reports


def render(report: dict, patient: tuple, path: Path) -> list[str]:
    """Draw the PDF; return the plain text of each page (ground truth for evals)."""
    pid, name, dob, phone, _ = patient
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    texts = []
    n = len(report["pages"])
    for pno, sections in enumerate(report["pages"], start=1):
        y = h - 60
        lines: list[str] = []

        def put(txt: str, bold: bool = False, size: int = 10):
            nonlocal y
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            c.drawString(50, y, txt)
            lines.append(txt)
            y -= 16 if size <= 10 else 22

        put(f"SAHYOG DIAGNOSTICS - {report['title']}", bold=True, size=13)
        if pno == 1:
            put("PATIENT DETAILS", bold=True)
            for row in (f"Name: {name}", f"Patient ID: {pid}", f"Date of birth: {dob}", f"Phone: {phone}",
                        f"Report date: {report['date']}"):
                put(row)
            y -= 8
        for header, body in sections:
            put(header, bold=True)
            for b in body:
                put(b)
            y -= 8
        put(f"Page {pno} of {n}", size=8)
        texts.append("\n".join(lines))
        c.showPage()
    c.save()
    return texts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk-patients", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261102)
    a = ap.parse_args()
    (OUT / "pdf").mkdir(parents=True, exist_ok=True)

    bulk_patients, bulk_reports = bulk(a.bulk_patients, a.seed)
    patients = {p[0]: p for p in PATIENTS + bulk_patients}
    with (OUT / "manifest.jsonl").open("w", encoding="utf-8") as mf:
        for rep in FIXTURE_REPORTS + bulk_reports:
            texts = render(rep, patients[rep["patient_id"]], OUT / "pdf" / f"{rep['doc_id']}.pdf")
            mf.write(json.dumps({k: rep[k] for k in ("doc_id", "patient_id", "type", "date", "title")}
                                | {"red_team": rep.get("red_team", False), "page_texts": texts,
                                   "file": f"pdf/{rep['doc_id']}.pdf"}) + "\n")
    with (OUT / "patients.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["patient_id", "name", "dob", "phone", "language", "synthetic"])
        w.writerows([(*p, "true") for p in patients.values()])
    with (OUT / "consents.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grantee_id", "patient_id", "scope", "purpose", "expires_in_days"])
        w.writerows([(g, p, "{" + ",".join(s) + "}", pur, d) for g, p, s, pur, d in CONSENTS])
    manifest = {"generator": GENERATOR_VERSION, "seed": a.seed, "patients": len(patients),
                "documents": len(FIXTURE_REPORTS) + len(bulk_reports), "fixture_documents": len(FIXTURE_REPORTS)}
    (OUT / "reports.manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()

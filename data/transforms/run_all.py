"""Run every transform on the raw Kaggle files and write data/clean/ plus the tally report.

Usage: python -m data.transforms.run_all [--no-scrub]
Expects data/raw/<slug>/ as laid out by the README download loop.
--no-scrub skips Presidio on transcriptions (only for a quick look; never load that output).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from data.transforms import disease_symptom, healthcare, medicines, noshows, transcriptions

ROOT = Path(__file__).resolve().parents[2]
RAW, CLEAN, REF = ROOT / "data" / "raw", ROOT / "data" / "clean", ROOT / "data" / "reference"


def find_csv(slug: str, hint: str = "") -> Path:
    files = sorted((RAW / slug).rglob("*.csv"))
    if hint:
        files = [f for f in files if hint in f.name.lower()] or files
    if not files:
        raise FileNotFoundError(f"no CSV under {RAW / slug}; download it first (README)")
    return files[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-scrub", action="store_true")
    a = ap.parse_args()
    CLEAN.mkdir(parents=True, exist_ok=True)
    clinics = pd.read_csv(REF / "clinics.csv")
    tallies = []

    out, t = noshows.transform(pd.read_csv(find_csv("noshowappointments")), clinics["clinic_id"].tolist())
    out.to_csv(CLEAN / "appointments_history.csv", index=False); tallies.append(t.as_dict())

    out, t = disease_symptom.transform(pd.read_csv(find_csv("disease-symptom-description-dataset", "dataset")),
                                       pd.read_csv(REF / "disease_specialty_crosswalk.csv"))
    out.to_csv(CLEAN / "symptom_specialty.csv", index=False); tallies.append(t.as_dict())

    scrub = (lambda s: s) if a.no_scrub else transcriptions.presidio_scrubber()
    out, t = transcriptions.transform(pd.read_csv(find_csv("medicaltranscriptions")),
                                      pd.read_csv(REF / "transcription_specialty_map.csv"), scrub)
    out["sections"] = out["sections"].map(json.dumps)
    out.to_csv(CLEAN / "transcriptions.csv", index=False); tallies.append(t.as_dict())
    transcriptions.sample_for_review(out).to_csv(CLEAN / "transcriptions_review_sample.csv", index=False)

    out, t = medicines.transform(pd.read_csv(find_csv("az-medicine-dataset-of-india")))
    out.to_csv(CLEAN / "formulary.csv", index=False); tallies.append(t.as_dict())

    hospitals = clinics.loc[clinics["kind"] == "hospital", "clinic_id"].tolist()
    out, t = healthcare.transform(pd.read_csv(find_csv("healthcare-dataset")), hospitals)
    out.to_csv(CLEAN / "encounter_history.csv", index=False); tallies.append(t.as_dict())

    (CLEAN / "tallies.json").write_text(json.dumps(tallies, indent=2, default=str))
    for x in tallies:
        print(f"{x['source']:16} in={x['rows_in']:>7,} out={x['rows_out']:>7,} dropped={x['dropped']}")


if __name__ == "__main__":
    main()

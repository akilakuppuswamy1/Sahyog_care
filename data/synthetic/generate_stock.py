"""Generate deterministic nightly per-clinic stock CSVs from the medicines transform."""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data.transforms.medicines import transform

RAW = ROOT / "data" / "raw" / "az-medicine-dataset-of-india" / "A_Z_medicines_dataset_of_India.csv"
CLINICS = ROOT / "data" / "reference" / "clinics.csv"
OUTPUT = ROOT / "data" / "synthetic" / "out" / "w3" / "stock"
FIELDS = ["medicine_id", "clinic_id", "name", "composition", "price_inr", "qty", "as_of", "source"]


def generate(seed: int = 20261102, as_of: str | None = None) -> dict:
    if not RAW.exists():
        raise FileNotFoundError(f"raw medicine dataset is required: {RAW}")
    formulary, _tally = transform(pd.read_csv(RAW))
    with CLINICS.open(encoding="utf-8", newline="") as stream:
        clinics = [row["clinic_id"] for row in csv.DictReader(stream)]
    if formulary.empty or not clinics:
        raise ValueError("the medicines transform and clinic reference must both be non-empty")
    inventory = formulary.to_dict(orient="records")
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in inventory:
        groups[str(row["composition"])].append(row)
    multi_brand = [(composition, rows) for composition, rows in groups.items() if len(rows) > 1]
    if not multi_brand:
        raise ValueError("the transformed formulary has no same-composition alternatives")
    if len(groups) < 2:
        raise ValueError("the transformed formulary needs multiple molecule groups")
    report_date = as_of or date.today().isoformat()
    rng = random.Random(seed)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for clinic_index, clinic_id in enumerate(clinics):
        quantities = {str(row["medicine_id"]): rng.randint(1, 80) for row in inventory}
        # Guarantee at least one unavailable brand with an in-stock same-molecule suggestion.
        pair_composition, pair = multi_brand[clinic_index % len(multi_brand)]
        quantities[str(pair[0]["medicine_id"])] = 0
        quantities[str(pair[1]["medicine_id"])] = max(1, quantities[str(pair[1]["medicine_id"])])
        # Some compositions are entirely absent at selected clinics; stock lookup must not infer availability.
        available_groups = [(composition, rows) for composition, rows in groups.items()
                            if composition != pair_composition]
        _, absent_group = available_groups[(clinic_index * 17) % len(available_groups)]
        for row in absent_group:
            quantities[str(row["medicine_id"])] = 0

        with (OUTPUT / f"{clinic_id}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in inventory:
                writer.writerow({
                    "medicine_id": row["medicine_id"], "clinic_id": clinic_id, "name": row["name"],
                    "composition": row["composition"], "price_inr": row["price_inr"],
                    "qty": quantities[str(row["medicine_id"])], "as_of": report_date, "source": "csv",
                })
    return {"generator": "w3-stock-v1", "seed": seed, "as_of": report_date,
            "clinics": len(clinics), "formulary_rows": len(inventory),
            "molecules": len(groups), "csv_files": len(clinics)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--as-of", help="inventory date (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()
    print(generate(args.seed, args.as_of))


if __name__ == "__main__":
    main()

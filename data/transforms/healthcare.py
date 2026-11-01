"""Healthcare Dataset, synthetic (Kaggle prasad22). Deck p.19.

Used as encounter history only.
Known mess -> fix:
  * random-case names ("bOBBy JacksOn")          -> names are dropped entirely; the patient master gets
                                                    generated names, so no source name ever reaches the DB
  * discharge before admission                   -> drop, tallied (reject, never repair). The proof is
                                                    that date-order violations fall to zero.
  * coarse test results ("Normal"/"Abnormal"/...) -> kept as a category; never presented as a lab value
  * hospital names unrelated to the network      -> mapped deterministically onto the 6 Sahyog hospitals
"""
from __future__ import annotations

import pandas as pd

from data.transforms.common import Tally, require_columns, stable_bucket

COLUMNS = {"Name", "Age", "Gender", "Medical Condition", "Date of Admission", "Hospital",
           "Admission Type", "Discharge Date", "Medication", "Test Results"}


def transform(raw: pd.DataFrame, hospital_ids: list[str]) -> tuple[pd.DataFrame, Tally]:
    require_columns(raw, COLUMNS, "healthcare")
    t = Tally("healthcare", rows_in=len(raw))
    df = raw.copy()
    df["admitted"] = pd.to_datetime(df["Date of Admission"], errors="coerce")
    df["discharged"] = pd.to_datetime(df["Discharge Date"], errors="coerce")
    df = t.drop("unparseable_date", df["admitted"].isna() | df["discharged"].isna(), df)
    df = t.drop("discharge_before_admission", df["discharged"] < df["admitted"], df)
    df = t.drop("implausible_age", (df["Age"] < 0) | (df["Age"] > 115), df)
    # Exact duplicates are a known property of this dataset.
    df = t.drop("exact_duplicate", df.drop(columns=["admitted", "discharged"]).duplicated(keep="first"), df)

    hospitals = sorted(hospital_ids)
    out = pd.DataFrame({
        "encounter_id": [f"HX{i:06d}" for i in range(1, len(df) + 1)],
        # Source identity is a hash of the name + age + gender, used only to group a person's encounters.
        "source_person_key": [f"P{stable_bucket(f'{n.strip().lower()}|{a}|{g}', 10**9):09d}"
                              for n, a, g in zip(df["Name"].astype(str), df["Age"], df["Gender"])],
        "age_at_admission": df["Age"].astype(int),
        "gender": df["Gender"].str[0].str.upper(),
        "condition": df["Medical Condition"].astype(str).str.strip(),
        "admission_type": df["Admission Type"].astype(str).str.strip(),
        "admitted_on": df["admitted"].dt.date,
        "discharged_on": df["discharged"].dt.date,
        "medication": df["Medication"].astype(str).str.strip(),
        "test_result_category": df["Test Results"].astype(str).str.strip(),
        "clinic_id": df["Hospital"].map(lambda h: hospitals[stable_bucket(str(h).strip().lower(), len(hospitals))]),
    }).reset_index(drop=True)

    t.rows_out = len(out)
    t.notes["date_order_violations_after"] = int((out["discharged_on"] < out["admitted_on"]).sum())
    t.check()
    return out, t

"""Disease Symptom Prediction (Kaggle itachi9604). Deck p.19.

Known mess -> fix:
  * wide Symptom_1..Symptom_17 columns          -> melted to long (disease, symptom)
  * stray underscores and spaces in symptoms   -> " skin_rash" becomes "skin rash"
  * trailing spaces in disease names           -> stripped ("Diabetes " becomes "Diabetes")
  * conditions with no specialty               -> joined to the reviewed crosswalk; unmapped is an error
  * duplicate (disease, symptom) pairs         -> collapsed, with a count of the rows behind each pair
"""
from __future__ import annotations

import re

import pandas as pd

from data.transforms.common import SchemaDrift, Tally, require_columns


def _clean_symptom(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip().lower()


def transform(raw: pd.DataFrame, crosswalk: pd.DataFrame) -> tuple[pd.DataFrame, Tally]:
    require_columns(raw, {"Disease", "Symptom_1"}, "disease_symptom")
    symptom_cols = [c for c in raw.columns if c.startswith("Symptom_")]
    t = Tally("disease_symptom", rows_in=len(raw))

    df = raw.copy()
    df["Disease"] = df["Disease"].astype(str).str.strip()
    long = df.melt(id_vars="Disease", value_vars=symptom_cols, value_name="symptom").dropna(subset=["symptom"])
    long["symptom"] = long["symptom"].astype(str).map(_clean_symptom)
    long = long[long["symptom"] != ""]
    pairs = (long.groupby(["Disease", "symptom"]).size().reset_index(name="support")
             .rename(columns={"Disease": "disease"}))

    cw = crosswalk.copy()
    cw["disease"] = cw["disease"].astype(str).str.strip()
    unmapped = sorted(set(pairs["disease"]) - set(cw["disease"]))
    if unmapped:
        raise SchemaDrift(f"diseases missing from the crosswalk: {unmapped}")

    out = pairs.merge(cw[["disease", "specialty_id", "alternative_specialty_id", "red_flag"]],
                      on="disease", how="left")
    # Long form changes the grain, so the tally counts source rows, not output pairs.
    t.rows_out = t.rows_in
    t.notes.update(pairs=len(out), diseases=out["disease"].nunique(), symptoms=out["symptom"].nunique(),
                   unreviewed_crosswalk_rows=int(cw["reviewed_by"].isna().sum()))
    t.check()
    return out, t

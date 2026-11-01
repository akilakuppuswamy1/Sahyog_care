"""A-Z Medicine Dataset of India (Kaggle shudhanshusingh). Deck p.19.

Known mess -> fix:
  * prices as strings with symbols ("₹1,250.50")    -> numeric INR; unparseable dropped, tallied
  * brand duplicates per composition                -> composition_key groups brands for substitute listing
  * pack size buried in text ("strip of 10 tablets") -> regex extraction; misses are counted for the LLM
                                                       fallback (deck target: regex and LLM agree on 95%
                                                       of a 200-row sample)
  * discontinued products                           -> drop, tallied (cannot be stocked)
The output is the formulary. The nightly per-clinic stock CSV is generated from it separately.
"""
from __future__ import annotations

import re

import pandas as pd

from data.transforms.common import SchemaDrift, Tally, require_columns

PACK = re.compile(
    r"(?:strip|bottle|vial|tube|packet|box|sachet|ampoule|cartridge|jar|pack|kit|bag|can|syringe)"
    r"\s+of\s+(\d+(?:\.\d+)?)\s*(ml|gm|g|mg|tablets?|capsules?|units?|softgels?|sachets?|pieces?|"
    r"injections?|kits?|lozenges?|suppositor(?:y|ies)|pessar(?:y|ies)|respules?|inhalers?|patch(?:es)?)?",
    re.IGNORECASE)
CURRENCY = re.compile(r"^\s*(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def parse_price(v) -> float | None:
    """'₹1,250.50' -> 1250.5; 'Rs. 12' -> 12.0. Anything else is None, never a guess.
    (A naive strip of non-digits turned 'Rs. 12' into 0.12; the test caught it.)"""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    s = CURRENCY.sub("", str(v)).replace(",", "").strip()
    return float(s) if NUMBER.match(s) else None


def parse_pack(label) -> tuple[float | None, str | None]:
    if pd.isna(label):
        return None, None
    m = PACK.search(str(label))
    if not m:
        return None, None
    unit = (m.group(2) or "unit").lower().rstrip("s") or "unit"
    return float(m.group(1)), unit


def composition_key(c1, c2) -> str:
    parts = [str(x).strip().lower() for x in (c1, c2) if pd.notna(x) and str(x).strip()]
    parts = [re.sub(r"\s+", " ", p) for p in parts]
    return " + ".join(sorted(parts))


def transform(raw: pd.DataFrame) -> tuple[pd.DataFrame, Tally]:
    price_cols = [c for c in raw.columns if c.lower().startswith("price")]
    if not price_cols:
        raise SchemaDrift(f"medicines: no price column in {sorted(raw.columns)}")
    require_columns(raw, {"id", "name", "Is_discontinued", "pack_size_label", "short_composition1"}, "medicines")
    t = Tally("medicines", rows_in=len(raw))
    df = raw.copy()

    df["price_inr"] = df[price_cols[0]].map(parse_price)
    df = t.drop("discontinued", df["Is_discontinued"].astype(str).str.upper().eq("TRUE"), df)
    df = t.drop("unparseable_price", df["price_inr"].isna() | (df["price_inr"] < 0), df)
    df["composition"] = [composition_key(a, b) for a, b in
                         zip(df["short_composition1"], df.get("short_composition2", pd.Series(index=df.index)))]
    df = t.drop("no_composition", df["composition"] == "", df)
    df = t.drop("duplicate_id", df.duplicated("id", keep="first"), df)

    packs = df["pack_size_label"].map(parse_pack)
    out = pd.DataFrame({
        "medicine_id": "MED" + df["id"].astype(str),
        "name": df["name"].astype(str).str.strip(),
        "manufacturer": df.get("manufacturer_name", pd.Series(index=df.index, dtype=str)),
        "dosage_form": df.get("type", pd.Series(index=df.index, dtype=str)),
        "composition": df["composition"],
        "price_inr": df["price_inr"].round(2),
        "pack_size": [p[0] for p in packs],
        "pack_unit": [p[1] for p in packs],
        "pack_size_label": df["pack_size_label"],
    }).reset_index(drop=True)

    t.rows_out = len(out)
    t.notes.update(compositions=int(out["composition"].nunique()),
                   pack_regex_miss=int(out["pack_size"].isna().sum()))
    t.check()
    return out, t

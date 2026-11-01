"""Medical Transcriptions (Kaggle tboyle10/mtsamples). Deck p.19.

Known mess -> fix:
  * template headers inside the body ("SUBJECTIVE:,") -> split into named sections, header commas removed
  * ~40 uneven specialty labels with leading spaces     -> mapped onto the 38 specialties via a reviewed file;
                                                           document-type labels kept but marked specialty-less
  * duplicates (the same note filed under two labels)   -> drop, tallied
  * empty transcriptions                                -> drop, tallied
  * names                                               -> scrubbed with Presidio (injected scrubber, so tests
                                                           run without the model download)
The proof (deck p.19) is "fifty notes read by eye for leftover identifiers": sample_for_review() picks them.
"""
from __future__ import annotations

import re
from typing import Callable

import pandas as pd

from data.transforms.common import SchemaDrift, Tally, require_columns

HEADER = re.compile(r"(?:(?<=^)|(?<=[.,]\s)|(?<=[.,]))\s*([A-Z][A-Z /&()-]{2,60}):\s*,?")
Scrubber = Callable[[str], str]


def split_sections(text: str) -> dict[str, str]:
    """'SUBJECTIVE:,  Pain.  PLAN:,  Rest.' -> {'SUBJECTIVE': 'Pain.', 'PLAN': 'Rest.'}"""
    parts = HEADER.split(text)
    sections: dict[str, str] = {}
    if parts[0].strip():
        sections["BODY"] = parts[0].strip(" ,")
    for name, body in zip(parts[1::2], parts[2::2]):
        key = name.strip()
        sections[key] = (sections.get(key, "") + " " + body.strip(" ,")).strip()
    return sections


def presidio_scrubber() -> Scrubber:  # pragma: no cover - needs the spaCy model
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    analyzer, anonymizer = AnalyzerEngine(), AnonymizerEngine()

    def scrub(text: str) -> str:
        hits = analyzer.analyze(text=text, language="en", entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS"])
        return anonymizer.anonymize(text=text, analyzer_results=hits).text
    return scrub


def transform(raw: pd.DataFrame, label_map: pd.DataFrame, scrub: Scrubber) -> tuple[pd.DataFrame, Tally]:
    require_columns(raw, {"description", "medical_specialty", "sample_name", "transcription"}, "transcriptions")
    t = Tally("transcriptions", rows_in=len(raw))
    df = raw.copy()
    df["medical_specialty"] = df["medical_specialty"].astype(str).str.strip()
    df = t.drop("empty_transcription", df["transcription"].isna() | (df["transcription"].astype(str).str.strip() == ""), df)
    df = t.drop("duplicate_text", df.duplicated("transcription", keep="first"), df)

    lm = label_map.copy()
    lm["source_label"] = lm["source_label"].astype(str).str.strip()
    unknown = sorted(set(df["medical_specialty"]) - set(lm["source_label"]))
    if unknown:
        raise SchemaDrift(f"labels missing from transcription_specialty_map.csv: {unknown}")
    df = df.merge(lm[["source_label", "specialty_id"]], left_on="medical_specialty",
                  right_on="source_label", how="left")

    scrubbed = df["transcription"].astype(str).map(scrub)
    out = pd.DataFrame({
        "note_id": [f"MT{i:05d}" for i in range(1, len(df) + 1)],
        "source_label": df["medical_specialty"],
        "specialty_id": df["specialty_id"].where(df["specialty_id"].notna(), None),
        "sample_name": df["sample_name"].astype(str).str.strip(),
        "sections": scrubbed.map(split_sections),
        "text": scrubbed.map(lambda s: re.sub(r"[ \t]+", " ", HEADER.sub(lambda m: f"\n{m.group(1)}: ", s)).strip()),
    })
    t.rows_out = len(out)
    t.notes.update(specialty_less=int(out["specialty_id"].isna().sum()),
                   labels=int(out["source_label"].nunique()))
    t.check()
    return out, t


def sample_for_review(clean: pd.DataFrame, n: int = 50, seed: int = 7) -> pd.DataFrame:
    return clean.sample(n=min(n, len(clean)), random_state=seed)[["note_id", "text"]]

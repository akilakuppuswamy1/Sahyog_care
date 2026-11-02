"""Generate deterministic W3 dictations and the 40-case fixed scribe fixture set."""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CLEAN = ROOT / "data" / "clean"
OUTPUT = ROOT / "data" / "synthetic" / "out" / "w3"
FIXTURES = ROOT / "evals" / "fixtures" / "w3" / "dictations.jsonl"
PLACEHOLDER = "[doctor to specify]"
PHONE = re.compile(r"(?<!\w)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\w)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mcg|μg|mg|g|ml|iu|units?)\b", re.I)
FREQUENCY = re.compile(
    r"\b(?:once|twice|thrice|one|two|three)\s+(?:daily|a day)|"
    r"\b(?:at night|every morning|every evening|before meals|after meals)\b",
    re.I,
)
WORDS = re.compile(r"[a-z0-9]+", re.I)
NAME_PATTERNS: dict[str, re.Pattern] = {}


def _safe_text(text: str) -> str:
    text = EMAIL.sub("[email removed]", text)
    text = PHONE.sub("[phone removed]", text)
    text = re.sub(r"(?im)^\s*(?:patient\s+)?(?:name|phone|mobile|email)\s*:\s*[^,\n]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _sections(row: dict) -> dict[str, str]:
    try:
        sections = json.loads(row.get("sections", "{}"))
    except json.JSONDecodeError:
        sections = {}
    return sections if isinstance(sections, dict) else {}


def _section(sections: dict[str, str], names: tuple[str, ...], fallback: str = "") -> str:
    for name in names:
        value = sections.get(name)
        if value:
            return _safe_text(str(value))
    return fallback


def gold_soap(row: dict) -> dict[str, str]:
    sections = _sections(row)
    source = _safe_text(row.get("text", ""))
    return {
        "S": _section(sections, ("SUBJECTIVE", "HISTORY OF PRESENT ILLNESS", "HISTORY"), source),
        "O": _section(sections, ("OBJECTIVE", "PHYSICAL EXAMINATION", "EXAMINATION")),
        "A": _section(sections, ("ASSESSMENT", "IMPRESSION", "DIAGNOSIS")),
        "P": _section(sections, ("PLAN", "TREATMENT")),
    }


def _formulary() -> list[dict[str, str]]:
    with (CLEAN / "formulary.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _formulary_index(formulary: list[dict[str, str]]) -> dict[tuple[str, ...], list[dict[str, str]]]:
    index: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for medicine in formulary:
        words = tuple(WORDS.findall(medicine["name"].casefold()))
        if words:
            index[words[:2]].append(medicine)
    return index


def gold_medicines(text: str, formulary_index: dict[tuple[str, ...], list[dict[str, str]]]) -> list[dict[str, str]]:
    text = _safe_text(text)
    words = WORDS.findall(text.casefold())
    search_keys = {(word,) for word in words}
    search_keys.update(zip(words, words[1:]))
    candidates = {}
    for key in search_keys:
        for medicine in formulary_index.get(key, ()):
            candidates[medicine["medicine_id"]] = medicine
    hits: list[tuple[int, int, dict[str, str]]] = []
    for medicine in candidates.values():
        name = medicine["name"].strip()
        if not name:
            continue
        pattern = NAME_PATTERNS.get(name)
        if pattern is None:
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
            NAME_PATTERNS[name] = pattern
        match = pattern.search(text)
        if match:
            hits.append((match.start(), match.end(), medicine))
    hits.sort(key=lambda hit: hit[0])
    results = []
    seen = set()
    for start, end, medicine in hits:
        identity = medicine["medicine_id"]
        if identity in seen:
            continue
        seen.add(identity)
        window_start, window_end = max(0, start - 24), min(len(text), end + 100)
        quote = text[window_start:window_end].strip()
        dose = DOSE.search(quote)
        frequency = FREQUENCY.search(quote)
        results.append({
            "name": medicine["name"], "molecule": medicine["composition"],
            "dose": dose.group(0) if dose else PLACEHOLDER,
            "frequency": frequency.group(0) if frequency else "",
            "quote": quote,
        })
    return results


def _fixtures(formulary: list[dict[str, str]]) -> list[dict]:
    branded = next((row for row in formulary if "augmentin 625 duo tablet" in row["name"].lower()), None)
    if branded is None:
        raise ValueError("the transformed formulary is missing Augmentin 625 Duo Tablet")
    cases = [
        ("overlapping-speakers", "Doctor: Start Atorva 10 mg at night. [overlapping] Patient: I take it after dinner. Doctor: Correct, after dinner.",
         {"name": "Atorva", "molecule": "atorvastatin", "dose": "10 mg", "frequency": "at night",
          "quote": "Atorva 10 mg at night"}, ["overlap", "brand_generic"]),
        ("class-only", "Doctor: Start a statin. Patient: Which one? Doctor: I will specify after review.",
         {"name": "statin", "molecule": PLACEHOLDER, "dose": PLACEHOLDER, "frequency": "",
          "quote": "statin"}, ["class_without_molecule"]),
        ("hinglish", "Doctor: Roz raat ko metformin 500 mg lena. Patient: Theek hai, main roz loonga.",
         {"name": "metformin", "molecule": "metformin", "dose": "500 mg", "frequency": "Roz raat ko",
          "quote": "Roz raat ko metformin 500 mg lena"}, ["hinglish"]),
        ("dose-correction", "Doctor: Take metformin 500 mg twice daily—sorry, make that 250 mg twice daily.",
         {"name": "metformin", "molecule": "metformin", "dose": "250 mg", "frequency": "twice daily",
          "quote": "metformin 500 mg twice daily—sorry, make that 250 mg twice daily"},
         ["dose_correction"]),
        ("indian-brand", f"Doctor: Take {branded['name']} 625 mg twice daily with food.",
         {"name": branded["name"], "molecule": branded["composition"], "dose": "625 mg",
          "frequency": "twice daily", "quote": f"Take {branded['name']} 625 mg twice daily"},
         ["brand_generic"]),
        ("unsupported-dose", "Doctor: Start metformin. Patient: Understood.",
         {"name": "metformin", "molecule": "metformin", "dose": PLACEHOLDER, "frequency": "",
          "quote": "Start metformin"}, ["unsupported_dose"]),
        ("unsupported-model-molecule", "Doctor: Use the tablet the patient brought from home; its label is unclear.",
         None, ["unsupported_molecule"]),
        ("no-medicine", "Doctor: The examination is reassuring. Continue hydration and return if symptoms worsen.",
         None, ["no_medicine"]),
    ]
    specialties = ["SP01", "SP02", "SP03", "SP04", "SP05", "SP06", "SP07", "SP08"]
    fixtures = []
    for index in range(40):
        _, transcript, medicine, tags = cases[index % len(cases)]
        transcript = f"{transcript} Doctor: Synthetic dictation case {index + 1}."
        row_medicines = [dict(medicine)] if medicine else []
        turns = list(re.finditer(r"\b(Doctor|Patient):\s*", transcript))
        patient_speech, doctor_speech = [], []
        for turn_index, turn in enumerate(turns):
            end = turns[turn_index + 1].start() if turn_index + 1 < len(turns) else len(transcript)
            content = transcript[turn.end():end].strip()
            (doctor_speech if turn.group(1) == "Doctor" else patient_speech).append(content)
        soap = {
            "S": " ".join(patient_speech),
            "O": "",
            "A": "",
            "P": " ".join(part.strip() for part in doctor_speech),
        }
        fixtures.append({
            "id": f"w3-{index + 1:03d}",
            "specialty_id": specialties[index % len(specialties)],
            "transcript": transcript,
            "gold_soap": soap,
            "gold_medicines": row_medicines,
            "tags": list(tags),
            "added_by": "synthetic generator",
            "version": 1,
        })
    return fixtures


def _bulk_records(seed: int, count: int,
                  formulary_index: dict[tuple[str, ...], list[dict[str, str]]]) -> list[dict]:
    source_path = CLEAN / "transcriptions.csv"
    with source_path.open(encoding="utf-8", newline="") as stream:
        source = list(csv.DictReader(stream))
    eligible = [row for row in source if row.get("text", "").strip()]
    if not eligible:
        raise ValueError(f"no cleaned transcription rows in {source_path}")
    rng = random.Random(seed)
    rng.shuffle(eligible)
    specialties = sorted({row.get("specialty_id", "") for row in eligible if row.get("specialty_id")})
    by_specialty = {specialty: [row for row in eligible if row.get("specialty_id") == specialty]
                    for specialty in specialties}
    chosen = []
    while len(chosen) < count:
        for specialty in specialties:
            rows = by_specialty[specialty]
            if rows and len(chosen) < count:
                chosen.append(rows[len(chosen) % len(rows)])
    records = []
    for index, row in enumerate(chosen, start=1):
        text = _safe_text(row["text"])
        soap = gold_soap(row)
        records.append({
            "id": f"w3-bulk-{index:04d}",
            "source_note_id": row["note_id"],
            "specialty_id": row.get("specialty_id") or None,
            "transcript": f"Doctor: {text} Patient: Yes, I understand. Doctor: End of dictation.",
            "gold_soap": soap,
            "gold_medicines": gold_medicines(text, formulary_index),
            "tags": ["cleaned_transcription_seed"],
            "added_by": "seeded synthetic generator",
            "version": 1,
        })
    return records


def generate(seed: int = 20261102, count: int = 300) -> dict:
    formulary = _formulary()
    formulary_index = _formulary_index(formulary)
    records = _bulk_records(seed, count, formulary_index)
    fixtures = _fixtures(formulary)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "dictations.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURES.open("w", encoding="utf-8", newline="\n") as stream:
        for fixture in fixtures:
            stream.write(json.dumps(fixture, ensure_ascii=False) + "\n")
    manifest = {"generator": "w3-dictations-v1", "seed": seed, "dictations": len(records),
                "fixtures": len(fixtures), "specialties": sorted({r["specialty_id"] for r in records
                                                                  if r["specialty_id"]})}
    (OUTPUT / "dictations.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--count", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(generate(args.seed, args.count), ensure_ascii=False))


if __name__ == "__main__":
    main()

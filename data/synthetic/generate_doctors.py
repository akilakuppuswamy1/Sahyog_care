"""Generate the 500-doctor directory (deck p.20).

Decision (logged in AI-COLLABORATION.md): the pack has an LLM write the directory. We build it
in seeded code instead. Names, languages and allocation are draws from explicit
distributions, so the output is reproducible byte for byte, auditable for bias, and costs
nothing. An LLM is reserved for the free-text layer (report PDFs, transcripts) where code
cannot do the job.

Rules enforced:
  * every specialty gets >= 10 doctors (D5 "enough data")
  * every clinic gets >= 1 doctor, so every clinic has slots every working day
  * hospital-only specialties sit in hospitals only
  * names are unique; every row passes the pydantic Doctor model or is rejected and counted

Usage: python data/synthetic/generate_doctors.py [--seed 20261102] [--out data/synthetic/out]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.schemas import Clinic, Doctor, Specialty  # noqa: E402

GENERATOR_VERSION = "doctors-v1"
MIN_PER_SPECIALTY = 10
TARGET_DOCTORS = 500

FIRST_F = ["Aditi", "Ananya", "Anjali", "Aparna", "Bhavana", "Deepa", "Divya", "Farah", "Gayatri",
           "Harini", "Ishita", "Jyoti", "Kavya", "Lakshmi", "Madhuri", "Meera", "Neha", "Nikita",
           "Pallavi", "Pooja", "Priyanka", "Radhika", "Rashmi", "Ritu", "Sameera", "Sana", "Shalini",
           "Shreya", "Sneha", "Sowmya", "Swati", "Tanvi", "Uma", "Vaishnavi", "Vidya", "Zoya"]
FIRST_M = ["Aakash", "Abhishek", "Aditya", "Ajay", "Amit", "Anil", "Arjun", "Ashwin", "Farhan",
           "Ganesh", "Harsha", "Imran", "Kiran", "Krishna", "Mahesh", "Manoj", "Naveen", "Nikhil",
           "Pradeep", "Prakash", "Rahul", "Rajesh", "Ravi", "Rohan", "Sameer", "Sanjay", "Siddharth",
           "Srinivas", "Suresh", "Tarun", "Varun", "Venkat", "Vikram", "Vinay", "Vivek", "Yusuf"]
SURNAME = {
    "Pune": ["Kulkarni", "Deshpande", "Joshi", "Patil", "Pawar", "Jadhav", "Gokhale", "Bhosale",
             "Shinde", "Apte", "Kale", "Deshmukh", "Gaikwad", "Chavan", "Sathe", "More", "Phadke",
             "Shah", "Mehta", "Iyer", "Khan", "Fernandes", "Agarwal", "Nair"],
    "Hyderabad": ["Reddy", "Rao", "Naidu", "Chowdary", "Goud", "Varma", "Sharma", "Murthy",
                  "Prasad", "Rangan", "Hussain", "Ali", "Siddiqui", "Kumar", "Yadav", "Raju",
                  "Shastri", "Pillai", "Iyer", "Mehta", "Khan", "Patel", "Sinha", "Menon"],
}
# Probability that a doctor in this city speaks the language (English is always spoken).
LANG_P = {"Pune": {"hi": 0.85, "mr": 0.65}, "Hyderabad": {"hi": 0.65, "te": 0.75, "ur": 0.20}}


def load_reference() -> tuple[list[Clinic], list[Specialty]]:
    ref = ROOT / "data" / "reference"
    with (ref / "clinics.csv").open() as f:
        clinics = [Clinic(**{**r, "has_pharmacy": r["has_pharmacy"] == "true"}) for r in csv.DictReader(f)]
    with (ref / "specialties.csv").open() as f:
        specs = [Specialty(**{**r, "hospital_only": r["hospital_only"] == "true"}) for r in csv.DictReader(f)]
    return clinics, specs


def allocate(clinics: list[Clinic], specs: list[Specialty], rng: random.Random) -> list[tuple[str, str]]:
    """Return (specialty_id, clinic_id) pairs, one per doctor."""
    hospitals = [c.clinic_id for c in clinics if c.kind == "hospital"]
    all_sites = [c.clinic_id for c in clinics]
    pairs: list[tuple[str, str]] = []
    for s in specs:
        n = MIN_PER_SPECIALTY + s.extra_doctors
        sites = hospitals if s.hospital_only else all_sites
        order = sites[:]
        rng.shuffle(order)
        pairs += [(s.specialty_id, order[i % len(order)]) for i in range(n)]

    # Guarantee every clinic has a doctor: move a general-practice doctor from the busiest site.
    counts = Counter(c for _, c in pairs)
    for site in all_sites:
        if counts[site] == 0:
            idx = next(i for i, (sp, c) in enumerate(pairs)
                       if sp in ("SP01", "SP02") and counts[c] > 1)
            counts[pairs[idx][1]] -= 1
            pairs[idx] = (pairs[idx][0], site)
            counts[site] += 1
    return pairs


def generate(seed: int) -> tuple[list[Doctor], list[dict]]:
    rng = random.Random(seed)
    clinics, specs = load_reference()
    city = {c.clinic_id: c.city for c in clinics}
    pairs = allocate(clinics, specs, rng)

    used_names: set[str] = set()
    doctors: list[Doctor] = []
    rejected: list[dict] = []
    for i, (sp, cl) in enumerate(pairs, start=1):
        gender = "F" if rng.random() < 0.5 else "M"
        first = rng.choice(FIRST_F if gender == "F" else FIRST_M)
        name = f"Dr. {first} {rng.choice(SURNAME[city[cl]])}"
        while name in used_names:  # repetition audit: never two identical names
            name = f"Dr. {first} {chr(65 + rng.randrange(26))}. {rng.choice(SURNAME[city[cl]])}"
        langs = ["en"] + [lang for lang, p in LANG_P[city[cl]].items() if rng.random() < p]
        row = dict(doctor_id=f"DR{i:04d}", name=name, gender=gender, specialty_id=sp, clinic_id=cl,
                   languages=tuple(langs), years_practice=rng.randint(2, 35))
        try:
            doctors.append(Doctor(**row))
            used_names.add(name)
        except Exception as e:  # reject, never repair (deck p.20 rule 4)
            rejected.append({"row": row, "error": str(e)})
    return doctors, rejected


def write(doctors: list[Doctor], rejected: list[dict], out: Path, seed: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "doctors.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doctor_id", "name", "gender", "specialty_id", "clinic_id", "languages",
                    "years_practice", "synthetic"])
        for d in doctors:
            w.writerow([d.doctor_id, d.name, d.gender, d.specialty_id, d.clinic_id,
                        "{" + ",".join(d.languages) + "}", d.years_practice, "true"])
    manifest = {"generator": GENERATOR_VERSION, "seed": seed, "rows": len(doctors),
                "rejected": len(rejected)}
    (out / "doctors.manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic" / "out")
    a = ap.parse_args()
    doctors, rejected = generate(a.seed)
    write(doctors, rejected, a.out, a.seed)


if __name__ == "__main__":
    main()

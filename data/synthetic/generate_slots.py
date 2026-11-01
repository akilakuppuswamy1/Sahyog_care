"""Build the slot calendar from clinic hours (deck p.20).

"A calendar is arithmetic, so no LLM touches it." 500 doctors x 60 working days x 16 slots
= 480,000 rows. Working days are Monday to Saturday. Each doctor works two 4-hour blocks of
30-minute slots (09:00-13:00 and 14:00-18:00 IST), which fit inside both clinic hours
(09:00-18:00) and hospital hours (08:00-20:00). A slot outside its clinic's hours is a bug,
and the generator raises rather than writing it.

Usage: python data/synthetic/generate_slots.py [--start 2026-11-16] [--days 60]
Needs data/synthetic/out/doctors.csv (run generate_doctors.py first).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.schemas import Slot  # noqa: E402
from data.synthetic.generate_doctors import load_reference  # noqa: E402

GENERATOR_VERSION = "slots-v1"
IST = ZoneInfo("Asia/Kolkata")
SLOT_MIN = 30
BLOCKS = [(time(9, 0), 8), (time(14, 0), 8)]  # (block start, slots in block) -> 16 a day
WORKING_WEEKDAYS = {0, 1, 2, 3, 4, 5}         # Mon-Sat


def working_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() in WORKING_WEEKDAYS:
            days.append(d)
        d += timedelta(days=1)
    return days


def slot_times(day: date) -> list[datetime]:
    out = []
    for block_start, count in BLOCKS:
        t0 = datetime.combine(day, block_start, tzinfo=IST)
        out += [t0 + timedelta(minutes=SLOT_MIN * i) for i in range(count)]
    return out


def generate(doctors_csv: Path, out_csv: Path, start: date, n_days: int, validate_every: int = 1) -> dict:
    clinics = {c.clinic_id: c for c in load_reference()[0]}
    days = working_days(start, n_days)
    rows = 0
    with doctors_csv.open() as f_in, out_csv.open("w", newline="") as f_out:
        w = csv.writer(f_out)
        w.writerow(["slot_id", "doctor_id", "clinic_id", "starts_at", "capacity", "synthetic"])
        for doc in csv.DictReader(f_in):
            clinic = clinics[doc["clinic_id"]]
            for day in days:
                for ts in slot_times(day):
                    end = (ts + timedelta(minutes=SLOT_MIN)).time()
                    if ts.time() < clinic.opens_at or end > clinic.closes_at:
                        raise ValueError(f"{doc['doctor_id']} slot {ts} outside {clinic.clinic_id} hours")
                    slot_id = f"{doc['doctor_id']}-{ts:%Y%m%d-%H%M}"
                    if rows % validate_every == 0:
                        Slot(slot_id=slot_id, doctor_id=doc["doctor_id"], clinic_id=clinic.clinic_id,
                             starts_at=ts)
                    w.writerow([slot_id, doc["doctor_id"], clinic.clinic_id, ts.isoformat(), 1, "true"])
                    rows += 1
    return {"generator": GENERATOR_VERSION, "start": start.isoformat(), "working_days": n_days,
            "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 16))
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic" / "out")
    a = ap.parse_args()
    manifest = generate(a.out / "doctors.csv", a.out / "slots.csv", a.start, a.days)
    (a.out / "slots.manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()

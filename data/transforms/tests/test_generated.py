"""Generated layer checks (deck p.20 rules 4-6) and the D5 "enough data" checks (deck p.22)."""
from __future__ import annotations

import csv
from collections import Counter
from datetime import date, datetime

import pytest

from data.synthetic import generate_doctors, generate_slots

SEED = 20261102


@pytest.fixture(scope="module")
def doctors():
    docs, rejected = generate_doctors.generate(SEED)
    assert rejected == []
    return docs


@pytest.fixture(scope="module")
def reference():
    return generate_doctors.load_reference()


def test_exactly_500_doctors_all_synthetic(doctors):
    assert len(doctors) == 500 and all(d.synthetic for d in doctors)


def test_generation_is_reproducible():
    a, _ = generate_doctors.generate(SEED)
    b, _ = generate_doctors.generate(SEED)
    assert a == b


def test_every_specialty_has_at_least_10_doctors(doctors, reference):
    counts = Counter(d.specialty_id for d in doctors)
    assert {s.specialty_id for s in reference[1]} == set(counts)
    assert min(counts.values()) >= 10


def test_every_clinic_has_a_doctor(doctors, reference):
    assert {c.clinic_id for c in reference[0]} <= {d.clinic_id for d in doctors}


def test_hospital_only_specialties_stay_in_hospitals(doctors, reference):
    clinics, specs = reference
    hospital_only = {s.specialty_id for s in specs if s.hospital_only}
    kind = {c.clinic_id: c.kind for c in clinics}
    assert all(kind[d.clinic_id] == "hospital" for d in doctors if d.specialty_id in hospital_only)


def test_names_unique(doctors):
    assert len({d.name for d in doctors}) == len(doctors)


def test_bias_audit_gender_mix_per_specialty(doctors):
    """No specialty may be all one gender; flagged, not silently accepted."""
    by_spec: dict[str, Counter] = {}
    for d in doctors:
        by_spec.setdefault(d.specialty_id, Counter())[d.gender] += 1
    lopsided = {s: dict(c) for s, c in by_spec.items() if min(c.get("F", 0), c.get("M", 0)) == 0}
    assert not lopsided, f"single-gender specialties: {lopsided}"


def test_language_mix_matches_city(doctors, reference):
    city = {c.clinic_id: c.city for c in reference[0]}
    for d in doctors:
        assert d.languages[0] == "en"
        assert not ({"te", "ur"} & set(d.languages) and city[d.clinic_id] == "Pune")
        assert not ("mr" in d.languages and city[d.clinic_id] == "Hyderabad")


def test_working_days_skip_sundays():
    days = generate_slots.working_days(date(2026, 11, 16), 60)
    assert len(days) == 60 and all(d.weekday() != 6 for d in days)


def test_sixteen_slots_a_day_inside_hours():
    times = generate_slots.slot_times(date(2026, 11, 16))
    assert len(times) == 16 and len(set(times)) == 16
    assert times[0].hour == 9 and times[-1].hour == 17 and times[-1].minute == 30


def test_slot_calendar_480k_and_every_clinic_every_day(tmp_path, doctors):
    docs_csv = tmp_path / "doctors.csv"
    with docs_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doctor_id", "clinic_id"])
        w.writerows([(d.doctor_id, d.clinic_id) for d in doctors])
    slots_csv = tmp_path / "slots.csv"
    manifest = generate_slots.generate(docs_csv, slots_csv, date(2026, 11, 16), 60, validate_every=997)
    assert manifest["rows"] == 480_000

    seen: dict[str, set] = {}
    ids = set()
    with slots_csv.open() as f:
        for r in csv.DictReader(f):
            ids.add(r["slot_id"])
            seen.setdefault(r["clinic_id"], set()).add(datetime.fromisoformat(r["starts_at"]).date())
    assert len(ids) == 480_000
    days = set(generate_slots.working_days(date(2026, 11, 16), 60))
    assert all(v == days for v in seen.values()) and len(seen) == 28

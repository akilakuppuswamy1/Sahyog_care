"""Seed distinct load-test patients and unused W3 encounters idempotently."""
from __future__ import annotations

import os

import psycopg

LOAD_PATIENT_COUNT = 1000
LOAD_ENCOUNTER_COUNT = 500


def seed_load_test_patients(conn: psycopg.Connection, count: int = LOAD_PATIENT_COUNT) -> int:
    inserted = 0
    for index in range(1, count + 1):
        cursor = conn.execute(
            """INSERT INTO patient (patient_id, name, dob, language, synthetic)
               VALUES (%s, %s, %s, 'en', true)
               ON CONFLICT (patient_id) DO NOTHING""",
            (f"LOAD-PT-{index:06d}", f"Synthetic Load Patient {index:06d}", "2000-01-01"),
        )
        inserted += max(cursor.rowcount, 0)
    return inserted


def seed_load_test_encounters(
    conn: psycopg.Connection, count: int = LOAD_ENCOUNTER_COUNT,
) -> int:
    patients = conn.execute(
        """SELECT p.patient_id FROM patient p
             WHERE p.synthetic AND EXISTS (
                   SELECT 1 FROM doc_chunk c WHERE c.patient_id = p.patient_id)
             ORDER BY p.patient_id"""
    ).fetchall()
    doctors = conn.execute(
        """SELECT doctor_id, clinic_id FROM doctor
             WHERE synthetic ORDER BY doctor_id"""
    ).fetchall()
    if not patients:
        raise RuntimeError(
            "No indexed synthetic patients are loaded; run `python tools/migrate.py` first."
        )
    if not doctors:
        raise RuntimeError(
            "No synthetic doctors are loaded; generate and load the doctor fixtures first."
        )

    encounter_rows = conn.execute(
        """SELECT e.encounter_id,
                  NOT EXISTS (SELECT 1 FROM note_draft nd
                               WHERE nd.encounter_id = e.encounter_id) AS available
             FROM encounter e WHERE e.encounter_id LIKE 'LOAD-%'"""
    ).fetchall()
    existing = {row[0] for row in encounter_rows}
    available = {row[0] for row in encounter_rows if row[1]}
    inserted = 0
    index = 0
    while len(available) < count:
        patient_id = patients[index % len(patients)][0]
        doctor_id, clinic_id = doctors[(index // len(patients)) % len(doctors)]
        encounter_id = f"LOAD-{patient_id}-{doctor_id}-{index:06d}"
        index += 1
        if encounter_id in existing:
            continue
        cursor = conn.execute(
            """INSERT INTO encounter
                   (encounter_id, patient_id, doctor_id, clinic_id, started_at, synthetic)
               VALUES (%s, %s, %s, %s, now(), true)
               ON CONFLICT (encounter_id) DO NOTHING""",
            (encounter_id, patient_id, doctor_id, clinic_id),
        )
        if cursor.rowcount:
            inserted += 1
            existing.add(encounter_id)
            available.add(encounter_id)
    return inserted


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running `python tools/seed_load_test.py`.")

    with psycopg.connect(database_url) as conn:
        patient_count = seed_load_test_patients(conn)
        encounter_count = seed_load_test_encounters(conn)
    print(
        f"Inserted {patient_count} synthetic load-test patient(s) and "
        f"{encounter_count} unused synthetic encounter(s)."
    )


if __name__ == "__main__":
    main()

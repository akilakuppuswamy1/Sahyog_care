"""Seed W3 load-test encounters from existing synthetic database rows."""
from __future__ import annotations

import os

import psycopg


def seed_load_test_encounters(conn: psycopg.Connection) -> int:
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

    inserted = 0
    for index, (patient_id,) in enumerate(patients):
        doctor_id, clinic_id = doctors[index % len(doctors)]
        cursor = conn.execute(
            """INSERT INTO encounter
                   (encounter_id, patient_id, doctor_id, clinic_id, started_at, synthetic)
               VALUES (%s, %s, %s, %s, now(), true)
               ON CONFLICT (encounter_id) DO NOTHING""",
            (f"LOAD-{patient_id}-{doctor_id}", patient_id, doctor_id, clinic_id),
        )
        inserted += max(cursor.rowcount, 0)
    return inserted


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running `python tools/seed_load_test.py`.")

    with psycopg.connect(database_url) as conn:
        count = seed_load_test_encounters(conn)
    print(f"Inserted {count} synthetic load-test encounter(s).")


if __name__ == "__main__":
    main()

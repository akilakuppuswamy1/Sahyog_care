-- Load the reference data and the generated layer. Run from the repo root:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f infra/db/002_load_generated.sql
-- Needs data/synthetic/out/ (python data/synthetic/generate_doctors.py && generate_slots.py).

BEGIN;

CREATE TEMP TABLE _clinic (clinic_id text, name text, city text, kind text, district text,
                           opens_at time, closes_at time, has_pharmacy boolean);
\copy _clinic FROM 'data/reference/clinics.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO clinic (clinic_id, name, city, kind, district, opens_at, closes_at, has_pharmacy)
SELECT * FROM _clinic;

CREATE TEMP TABLE _spec (specialty_id text, name text, hospital_only boolean, extra_doctors int);
\copy _spec FROM 'data/reference/specialties.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO specialty (specialty_id, name) SELECT specialty_id, name FROM _spec;

\copy doctor (doctor_id, name, gender, specialty_id, clinic_id, languages, years_practice, synthetic) FROM 'data/synthetic/out/doctors.csv' WITH (FORMAT csv, HEADER true)
\copy slot (slot_id, doctor_id, clinic_id, starts_at, capacity, synthetic) FROM 'data/synthetic/out/slots.csv' WITH (FORMAT csv, HEADER true)

-- The D5 checks, as queries: each must return zero rows.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM specialty s LEFT JOIN doctor d USING (specialty_id)
               GROUP BY s.specialty_id HAVING count(d.doctor_id) < 10) THEN
        RAISE EXCEPTION 'D5: a specialty has fewer than 10 doctors';
    END IF;
    IF EXISTS (SELECT 1 FROM clinic c
               CROSS JOIN (SELECT DISTINCT (starts_at AT TIME ZONE 'Asia/Kolkata')::date AS d FROM slot) days
               WHERE NOT EXISTS (SELECT 1 FROM slot s WHERE s.clinic_id = c.clinic_id
                                 AND (s.starts_at AT TIME ZONE 'Asia/Kolkata')::date = days.d)) THEN
        RAISE EXCEPTION 'D5: a clinic has a working day with no slots';
    END IF;
END $$;

COMMIT;

SELECT 'clinic' AS t, count(*) FROM clinic UNION ALL SELECT 'specialty', count(*) FROM specialty
UNION ALL SELECT 'doctor', count(*) FROM doctor UNION ALL SELECT 'slot', count(*) FROM slot;

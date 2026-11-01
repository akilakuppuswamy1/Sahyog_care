-- Migration for databases created before this fix. Fresh databases get it from 001_schema.sql.
-- forbid_change() raised SQLSTATE P0002 (no_data_found), which clients read as "no rows",
-- not as a refused write. It now raises P0001 (raise_exception). Behaviour is unchanged:
-- signed notes and audit rows still cannot be updated or deleted.
CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: rows are immutable', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'P0001';
END;
$$;

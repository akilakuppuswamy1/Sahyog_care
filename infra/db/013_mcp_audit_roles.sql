BEGIN;

ALTER TABLE audit_event DROP CONSTRAINT audit_event_actor_role_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_actor_role_check
    CHECK (actor_role IN
           ('patient','caregiver','doctor','front_desk','ops','pharmacist','compliance','system'));

COMMIT;

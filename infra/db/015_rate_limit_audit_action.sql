BEGIN;

ALTER TABLE audit_event DROP CONSTRAINT IF EXISTS audit_event_action_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_action_check
    CHECK (action IN ('read','write','delete','sign','override','escalate','rate_limited'));

COMMIT;

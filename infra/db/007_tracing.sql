BEGIN;

CREATE TABLE trace_span (
    trace_id        text NOT NULL,
    span_id         text PRIMARY KEY,
    parent_span_id  text,
    workflow        text NOT NULL,
    name            text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('node', 'tool', 'model', 'http')),
    started_at      timestamptz NOT NULL,
    duration_ms     double precision NOT NULL CHECK (duration_ms >= 0),
    status          text NOT NULL CHECK (status IN ('ok', 'error', 'timeout', 'degraded')),
    model           text,
    provider        text,
    served_by       text,
    tokens_in       integer CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out      integer CHECK (tokens_out IS NULL OR tokens_out >= 0),
    error_type      text
);
CREATE INDEX trace_span_workflow_started_idx ON trace_span (workflow, started_at);
CREATE INDEX trace_span_trace_started_idx ON trace_span (trace_id, started_at);

CREATE FUNCTION purge_trace_spans()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    removed bigint;
BEGIN
    DELETE FROM trace_span WHERE started_at < now() - interval '30 days';
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END;
$$;

CREATE FUNCTION audit_event_trace_context()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.trace_id := coalesce(NEW.trace_id, nullif(NEW.detail->>'trace_id', ''), gen_random_uuid()::text);
    IF NOT (NEW.detail ? 'trace_id') THEN
        NEW.detail := jsonb_set(coalesce(NEW.detail, '{}'::jsonb), '{trace_id}', to_jsonb(NEW.trace_id), true);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER audit_event_trace_context_before_insert
    BEFORE INSERT ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_trace_context();

COMMIT;

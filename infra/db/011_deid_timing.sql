ALTER TABLE trace_span ADD COLUMN deid_ms double precision CHECK (deid_ms >= 0 AND deid_ms < 'Infinity'::double precision);

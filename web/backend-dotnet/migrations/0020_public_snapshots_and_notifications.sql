ALTER TABLE compute_jobs ADD COLUMN trace_id varchar(64);
ALTER TABLE compute_jobs ADD COLUMN claimed_at timestamptz;
ALTER TABLE compute_jobs ADD COLUMN timings_json jsonb;

CREATE TABLE public_compute_snapshots (
    kind text NOT NULL CHECK (kind IN ('market.snapshot','market.quotes','market.news','fund.detail','fund.valuations')),
    input_hash text NOT NULL,
    input_json jsonb NOT NULL,
    result_json jsonb NOT NULL,
    completed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (kind,input_hash)
);

-- Only anonymous provider results can enter the shared display snapshot table.
CREATE FUNCTION quant_compute_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ttl integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_notify('quant_compute_queued','queued');
        RETURN NEW;
    END IF;
    IF NEW.status = 'running' AND OLD.status <> 'running' THEN
        NEW.claimed_at := now();
    END IF;
    IF NEW.status IN ('completed','failed','cancelled','interrupted') AND NEW.status IS DISTINCT FROM OLD.status THEN
        IF NEW.status = 'completed' AND NEW.result_json IS NOT NULL
            AND NEW.kind IN ('market.snapshot','market.quotes','market.news','fund.detail','fund.valuations')
            AND NOT NEW.input_json ?| ARRAY['snapshot','credentials','_execution']
            AND NOT NEW.result_json ?| ARRAY['user_id','snapshot','credentials']
            AND (CASE NEW.kind
                WHEN 'market.snapshot' THEN jsonb_typeof(NEW.result_json->'indices')='array' AND jsonb_typeof(NEW.result_json->'fund_rankings')='array' AND jsonb_typeof(NEW.result_json->'sector_rankings')='array' AND NEW.result_json ? 'observed_at'
                WHEN 'fund.detail' THEN jsonb_typeof(NEW.result_json->'nav_history')='array' AND NEW.result_json ?& ARRAY['symbol','name','observed_at']
                ELSE jsonb_typeof(NEW.result_json->'items')='array' AND NEW.result_json ? 'unavailable'
            END)
            AND COALESCE(NEW.result_json->>'stale','false') <> 'true'
            AND (NEW.kind <> 'market.snapshot' OR jsonb_array_length(COALESCE(NEW.result_json->'unavailable','[]'::jsonb)) < 3) THEN
            ttl := CASE NEW.kind WHEN 'market.snapshot' THEN 60 WHEN 'fund.detail' THEN 300 ELSE 60 END;
            INSERT INTO public_compute_snapshots(kind,input_hash,input_json,result_json,completed_at,expires_at)
            VALUES(NEW.kind,NEW.input_hash,NEW.input_json - '_refresh',NEW.result_json,NEW.completed_at,NEW.completed_at+make_interval(secs=>ttl))
            ON CONFLICT(kind,input_hash) DO UPDATE SET input_json=EXCLUDED.input_json,result_json=EXCLUDED.result_json,
                completed_at=EXCLUDED.completed_at,expires_at=EXCLUDED.expires_at
            WHERE public_compute_snapshots.completed_at<=EXCLUDED.completed_at;
        END IF;
        PERFORM pg_notify('quant_compute_completed',json_build_object('user_id',NEW.user_id,'job_id',NEW.job_id)::text);
        PERFORM pg_notify('quant_compute_queued','released');
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER compute_job_claim_time BEFORE UPDATE ON compute_jobs
    FOR EACH ROW WHEN (NEW.status='running' AND OLD.status<>'running') EXECUTE FUNCTION quant_compute_events();
CREATE TRIGGER compute_job_notifications AFTER INSERT OR UPDATE ON compute_jobs
    FOR EACH ROW EXECUTE FUNCTION quant_compute_events();

CREATE INDEX public_snapshot_expiry ON public_compute_snapshots(expires_at);
CREATE INDEX public_snapshot_input ON public_compute_snapshots(kind,input_json,completed_at DESC);

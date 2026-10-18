ALTER TABLE public_compute_snapshots DROP CONSTRAINT public_compute_snapshots_kind_check;
ALTER TABLE public_compute_snapshots ADD CONSTRAINT public_compute_snapshots_kind_check CHECK (kind IN ('market.snapshot','market.quotes','market.news','market.history','fund.detail','fund.valuations'));

CREATE OR REPLACE FUNCTION quant_compute_events() RETURNS trigger LANGUAGE plpgsql AS $$
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
            AND NEW.kind IN ('market.snapshot','market.quotes','market.news','market.history','fund.detail','fund.valuations')
            AND NOT NEW.input_json ?| ARRAY['snapshot','credentials','_execution']
            AND NOT NEW.result_json ?| ARRAY['user_id','snapshot','credentials']
            AND (CASE NEW.kind
                WHEN 'market.snapshot' THEN jsonb_typeof(NEW.result_json->'indices')='array' AND jsonb_typeof(NEW.result_json->'fund_rankings')='array' AND jsonb_typeof(NEW.result_json->'sector_rankings')='array' AND NEW.result_json ? 'observed_at'
                WHEN 'market.history' THEN jsonb_typeof(NEW.result_json->'items')='array' AND jsonb_array_length(NEW.result_json->'items')>0 AND NEW.result_json ?& ARRAY['symbol','name','source','observed_at','unavailable']
                WHEN 'fund.detail' THEN jsonb_typeof(NEW.result_json->'nav_history')='array' AND NEW.result_json ?& ARRAY['symbol','name','observed_at']
                ELSE jsonb_typeof(NEW.result_json->'items')='array' AND NEW.result_json ? 'unavailable'
            END)
            AND COALESCE(NEW.result_json->>'stale','false') <> 'true'
            AND (NEW.kind <> 'market.snapshot' OR jsonb_array_length(COALESCE(NEW.result_json->'unavailable','[]'::jsonb)) < 3) THEN
            ttl := CASE NEW.kind WHEN 'market.snapshot' THEN 60 WHEN 'fund.detail' THEN 300 WHEN 'market.history' THEN 3600 ELSE 60 END;
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


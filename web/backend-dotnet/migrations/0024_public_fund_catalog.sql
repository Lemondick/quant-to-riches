-- Derived public cache only; no account, NAV, ledger or transaction mutation.
ALTER TABLE public_compute_snapshots DROP CONSTRAINT public_compute_snapshots_kind_check;
ALTER TABLE public_compute_snapshots ADD CONSTRAINT public_compute_snapshots_kind_check CHECK (kind IN ('market.snapshot','market.quotes','market.news','market.history','fund.detail','fund.valuations','fund.catalog'));

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
            AND NEW.kind IN ('market.snapshot','market.quotes','market.news','market.history','fund.detail','fund.valuations','fund.catalog')
            AND NOT NEW.input_json ?| ARRAY['snapshot','credentials','_execution']
            AND NOT NEW.result_json ?| ARRAY['user_id','snapshot','credentials']
            AND (CASE NEW.kind
                WHEN 'market.snapshot' THEN jsonb_typeof(NEW.result_json->'indices')='array' AND jsonb_typeof(NEW.result_json->'fund_rankings')='array' AND jsonb_typeof(NEW.result_json->'sector_rankings')='array' AND NEW.result_json ? 'observed_at'
                WHEN 'market.history' THEN jsonb_typeof(NEW.result_json->'items')='array' AND jsonb_array_length(NEW.result_json->'items')>0 AND NEW.result_json ?& ARRAY['symbol','name','source','observed_at','unavailable']
                WHEN 'fund.catalog' THEN
                    NEW.result_json->>'schema_version'='fund-directory.v1'
                    AND NEW.result_json->>'source'='eastmoney'
                    AND NEW.result_json->>'quality_status' IN ('unknown_time','valid')
                    AND NEW.result_json->'source_time'='null'::jsonb
                    AND jsonb_typeof(NEW.result_json->'fetched_at')='string'
                    AND CASE WHEN jsonb_typeof(NEW.result_json->'items')='array' THEN
                        jsonb_array_length(NEW.result_json->'items') BETWEEN 1 AND 50000
                        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.result_json->'items') item
                            WHERE jsonb_typeof(item)<>'object' OR item - ARRAY['symbol','name','fund_type']<>'{}'::jsonb
                                OR COALESCE(item->>'symbol','') !~ '^[0-9]{6}$'
                                OR COALESCE(item->>'name','')='' )
                    ELSE false END
                WHEN 'fund.detail' THEN jsonb_typeof(NEW.result_json->'nav_history')='array' AND NEW.result_json ?& ARRAY['symbol','name','observed_at']
                ELSE jsonb_typeof(NEW.result_json->'items')='array' AND NEW.result_json ? 'unavailable'
            END)
            AND (CASE WHEN NEW.kind='market.history' THEN
                COALESCE(NEW.result_json->>'quality_status','valid') IN ('valid','stale','unknown_time')
                AND COALESCE(NEW.result_json->>'fallback_used','false')<>'true'
                AND COALESCE(NEW.result_json->>'cache_fallback','false')<>'true'
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.result_json->'items') item WHERE item->>'date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
                ELSE COALESCE(NEW.result_json->>'stale','false')<>'true' END)
            AND (NEW.kind <> 'market.snapshot' OR jsonb_array_length(COALESCE(NEW.result_json->'unavailable','[]'::jsonb)) < 3) THEN
            ttl := CASE NEW.kind WHEN 'market.snapshot' THEN 60 WHEN 'fund.detail' THEN 300 WHEN 'market.history' THEN 3600 WHEN 'fund.catalog' THEN 21600 ELSE 60 END;
            INSERT INTO public_compute_snapshots(kind,input_hash,input_json,result_json,completed_at,expires_at)
            VALUES(NEW.kind,NEW.input_hash,NEW.input_json - '_refresh',NEW.result_json,NEW.completed_at,NEW.completed_at+make_interval(secs=>ttl))
            ON CONFLICT(kind,input_hash) DO UPDATE SET input_json=EXCLUDED.input_json,result_json=EXCLUDED.result_json,
                completed_at=EXCLUDED.completed_at,expires_at=EXCLUDED.expires_at
            WHERE public_compute_snapshots.completed_at<=EXCLUDED.completed_at
                AND (NEW.kind<>'market.history' OR
                    (SELECT max(item->>'date') FROM jsonb_array_elements(EXCLUDED.result_json->'items') item)
                    >= COALESCE((SELECT max(item->>'date') FROM jsonb_array_elements(public_compute_snapshots.result_json->'items') item),''));
        END IF;
        PERFORM pg_notify('quant_compute_completed',json_build_object('user_id',NEW.user_id,'job_id',NEW.job_id)::text);
        PERFORM pg_notify('quant_compute_queued','released');
    END IF;
    RETURN NEW;
END;
$$;

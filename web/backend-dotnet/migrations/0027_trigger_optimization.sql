-- 0027_trigger_optimization.sql（E8：触发器优化）
--
-- 本迁移只做 DROP TRIGGER IF EXISTS + 重建；0001–0024 原样保留（grandfather）。
-- 业务语义保持不变，仅降低触发器开销：
--   ① 0020/0024 的 AFTER 触发器原来对每次 INSERT/UPDATE 逐行执行 quant_compute_events()，
--      无 WHEN 过滤；现在拆分为两个窄触发器：INSERT 只发 queued 通知，
--      UPDATE 只在终态流转（completed/failed/cancelled/interrupted）时执行。
--      notify 语义不变：INSERT 发 1 条 quant_compute_queued='queued'；
--      终态流转发 quant_compute_completed + quant_compute_queued='released' 各 1 条。
--   ② fund.catalog 的逐元素正则校验（上限 5 万元素、触发器内逐个 !~ 校验）移出触发器：
--      触发器内只保留廉价的结构 + 数量上限检查（jsonb_array_length BETWEEN 1 AND 50000）；
--      逐元素的 symbol/name 格式校验约定由 worker/应用层在落库前完成（见 web/docs/迁移规范.md）。
--      若应用层未做该校验，fund.catalog 行仍保证结构完整但不再保证逐元素格式。
--   ③ 0021 的行级 quant_account_display_changed() 改为语句级批量合并：
--      批量导入 positions/instruments/user_documents 时，每个受影响用户每条语句只更新一次
--      data_revision，避免逐行 UPDATE users 造成的行锁争用。data_revision 是单调递增的
--      缓存失效版本号，精确的递增次数不具业务含义，因此按用户合并递增语义一致。

-- ============ ①② quant_compute_events：去掉 fund.catalog 逐元素校验 ============
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

-- ============ ① AFTER 触发器加 WHEN 子句（拆分 INSERT / 终态 UPDATE） ============
-- compute_job_claim_time（BEFORE UPDATE，0020 已带 WHEN 子句）保持不动。
DROP TRIGGER IF EXISTS compute_job_notifications ON compute_jobs;

CREATE TRIGGER compute_job_queued
    AFTER INSERT ON compute_jobs
    FOR EACH ROW
    EXECUTE FUNCTION quant_compute_events();

CREATE TRIGGER compute_job_terminal
    AFTER UPDATE ON compute_jobs
    FOR EACH ROW
    WHEN (NEW.status IN ('completed','failed','cancelled','interrupted')
          AND NEW.status IS DISTINCT FROM OLD.status)
    EXECUTE FUNCTION quant_compute_events();

-- ============ ③ 0021 行级触发器改为语句级批量合并 ============
DROP TRIGGER IF EXISTS position_display_revision ON positions;
DROP TRIGGER IF EXISTS instrument_display_revision ON instruments;
DROP TRIGGER IF EXISTS document_display_revision ON user_documents;
DROP FUNCTION IF EXISTS quant_account_display_changed();

CREATE OR REPLACE FUNCTION quant_account_display_changed_stmt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE users AS u
    SET data_revision = data_revision + 1
    FROM (
        SELECT DISTINCT user_id FROM old_rows
        UNION
        SELECT DISTINCT user_id FROM new_rows
    ) AS v(user_id)
    WHERE u.id = v.user_id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER position_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON positions
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

CREATE TRIGGER instrument_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON instruments
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

CREATE TRIGGER document_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON user_documents
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

-- 0035_snapshot_retention.down.sql — 回滚 0035：把分区表重建回普通表。
-- 幂等：非分区表时跳过重建，只删除函数与索引。
-- 注意：回滚同样需要停机窗口（重建期间停止写入者），否则迁移期间的写入会丢失；
-- 如对数据有任何疑虑，优先从 F2 备份恢复而不是执行回滚。

-- 先删覆盖索引（分区父表上的索引，表改名后索引名不变，提前删避免后续重名）。
DROP INDEX IF EXISTS public.native_monitor_samples_recent_covering;

DO $$
DECLARE
    is_partitioned boolean;
    old_count bigint := 0;
    new_count bigint := 0;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        WHERE c.relname = 'native_monitor_samples'
          AND c.relnamespace = 'public'::regnamespace
    ) INTO is_partitioned;

    IF is_partitioned THEN
        CREATE TABLE public.native_monitor_samples_plain (
            captured_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (captured_at)
        );
        INSERT INTO public.native_monitor_samples_plain
            SELECT captured_at, payload FROM public.native_monitor_samples;
        SELECT count(*) INTO old_count FROM public.native_monitor_samples;
        SELECT count(*) INTO new_count FROM public.native_monitor_samples_plain;
        IF old_count <> new_count THEN
            RAISE EXCEPTION '0035 down: 回滚行数不一致 partitioned=% plain=%，请从备份恢复',
                old_count, new_count;
        END IF;
        -- 改名顺序：先挪走分区表（含其 native_monitor_samples_pkey 父索引），
        -- 再把新表扶正并恢复默认主键索引名，最后删掉旧分区表。
        ALTER TABLE public.native_monitor_samples RENAME TO native_monitor_samples_partitioned;
        ALTER INDEX IF EXISTS public.native_monitor_samples_pkey
            RENAME TO native_monitor_samples_partitioned_pkey;
        ALTER TABLE public.native_monitor_samples_plain RENAME TO native_monitor_samples;
        ALTER INDEX public.native_monitor_samples_plain_pkey RENAME TO native_monitor_samples_pkey;
        DROP TABLE public.native_monitor_samples_partitioned;
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS public.quant_snapshot_maintenance();
DROP FUNCTION IF EXISTS public.quant_cleanup_snapshot_partitions(integer);
DROP FUNCTION IF EXISTS public.quant_ensure_snapshot_partitions(integer);

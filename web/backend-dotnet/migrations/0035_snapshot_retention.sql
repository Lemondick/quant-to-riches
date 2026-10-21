-- 0035_snapshot_retention.sql — 任务9：快照表保留策略（分钟/tick 级分区 + 90 天保留）。
--
-- 背景：native_monitor_samples 由 NativeRuntimeMaintenance 每分钟写入一行
-- （captured_at = date_trunc('minute', now())），是唯一无界增长的 tick/分钟级快照表。
-- 日线及以上快照（data_snapshots、fund_lot_snapshots、advisor_decision_snapshots、
-- fund_research_snapshots）及 TTL 缓存（public_compute_snapshots、agent_preview_snapshots）
-- 永久保留，不做分区（见 web/docs/维护说明.md §13 快照保留策略）。
--
-- 本迁移：
--   ① 把 native_monitor_samples 重建为按月 RANGE 分区表（分区键 captured_at）；
--   ② 预建未来 3 个月分区 + DEFAULT 分区兜底；
--   ③ 新增覆盖索引（(captured_at DESC) INCLUDE (payload)，供趋势查询 index-only 扫描）；
--   ④ 新增维护函数 quant_ensure_snapshot_partitions / quant_cleanup_snapshot_partitions /
--      quant_snapshot_maintenance，供 systemd timer（deploy/quant-snapshot-cleanup.*）每日调用。
--      保留天数默认 90，可用环境变量 QUANT_SNAPSHOT_RETENTION_TICK_DAYS 覆盖
--      （timer 经 GUC quant.snapshot_retention_tick_days 传入）。
--
-- 说明：原计划用 `-- quant:no-transaction` 标记 + CREATE INDEX CONCURRENTLY，
-- 但 PG（18 已实测）不支持在分区表上并发建索引
-- （ERROR: cannot create index on partitioned table concurrently），
-- 故覆盖索引改为普通构建——迁移时分区均为新建小分区，构建极快，
-- 且本迁移本来就要求停机窗口（见下）。文件整体在 Runner 事务内执行，
-- 回填行数校验失败会整体回滚。
--
-- 生产执行假设（高风险操作，详见维护说明.md §13）：
--   - 执行前已做完整备份（F2 备份链）并在 staging 演练过本迁移；
--   - 停机窗口内停止写入者（后端 API / QUANT_DISABLE_MAINTENANCE=true），
--     迁移期间写入旧表的数据不会自动进入新分区表；
--   - 用 Runner 执行：dotnet QuantAssistant.Api.dll --migrate migrations
--     （本文件在显式事务内执行：回填行数校验失败会整体回滚；
--     事务内 DDL 持有锁时间短，但重建期间仍需停写入者，见下）。
-- 回滚：执行 0035_snapshot_retention.down.sql（重建为普通表），或从备份恢复。
--
-- 幂等：重复执行安全（分区表已存在则跳过重建；函数/索引用 IF NOT EXISTS / OR REPLACE）。

-- ============ 维护函数（先定义，重建块与 timer 都会用到） ============

-- 预建未来月份分区。只建缺失的月份分区；返回新建数量。
CREATE OR REPLACE FUNCTION public.quant_ensure_snapshot_partitions(months_ahead integer DEFAULT 3)
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    start_m date := date_trunc('month', now())::date;
    m date;
    part_name text;
    created_count integer := 0;
BEGIN
    IF months_ahead IS NULL OR months_ahead < 0 THEN
        months_ahead := 0;
    END IF;
    FOR i IN 0..months_ahead LOOP
        m := start_m + make_interval(months => i);
        part_name := 'native_monitor_samples_p' || to_char(m, 'YYYYMM');
        IF to_regclass('public.' || part_name) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.native_monitor_samples FOR VALUES FROM (%L) TO (%L)',
                part_name, m::timestamptz, (m + make_interval(months => 1))::timestamptz);
            created_count := created_count + 1;
        END IF;
    END LOOP;
    RETURN created_count;
END;
$$;

-- 按保留期删除过期月份分区。只删除命名规范（native_monitor_samples_pYYYYMM）且
-- 分区上界 <= now() - retention_days 的分区；DEFAULT 分区与手工分区永不删除。
-- 返回已删除分区名（逗号分隔），无删除时返回 'none'。
CREATE OR REPLACE FUNCTION public.quant_cleanup_snapshot_partitions(retention_days integer DEFAULT 90)
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    cutoff timestamptz := now() - make_interval(days => retention_days);
    r record;
    part_month date;
    upper_bound timestamptz;
    dropped text[] := '{}';
BEGIN
    IF retention_days IS NULL OR retention_days < 1 THEN
        RAISE EXCEPTION 'quant_cleanup_snapshot_partitions: retention_days 必须 >= 1，得到 %', retention_days;
    END IF;
    FOR r IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'native_monitor_samples'
          AND p.relnamespace = 'public'::regnamespace
          AND c.relname LIKE 'native_monitor_samples\_p______' ESCAPE '\'
    LOOP
        BEGIN
            part_month := to_date(right(r.relname, 6), 'YYYYMM');
        EXCEPTION WHEN OTHERS THEN
            CONTINUE; -- 命名不规范的分区跳过，永不误删
        END;
        upper_bound := part_month + make_interval(months => 1);
        IF upper_bound <= cutoff THEN
            EXECUTE format('DROP TABLE public.%I', r.relname);
            dropped := dropped || r.relname;
        END IF;
    END LOOP;
    IF array_length(dropped, 1) IS NULL THEN
        RETURN 'none';
    END IF;
    RETURN 'dropped: ' || array_to_string(dropped, ', ');
END;
$$;

-- 每日维护入口：读保留期（GUC quant.snapshot_retention_tick_days，默认 90），
-- 预建未来分区 + 清理过期分区。返回人类可读摘要，供 timer 日志记录。
CREATE OR REPLACE FUNCTION public.quant_snapshot_maintenance()
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    retention integer := 90;
    raw text := current_setting('quant.snapshot_retention_tick_days', true);
    ensured integer;
    cleaned text;
BEGIN
    IF raw IS NOT NULL AND raw ~ '^[0-9]+$' THEN
        retention := GREATEST(1, LEAST(3650, raw::integer));
    END IF;
    ensured := public.quant_ensure_snapshot_partitions(3);
    cleaned := public.quant_cleanup_snapshot_partitions(retention);
    RETURN format('snapshot maintenance: retention_days=%s, ensured_partitions=%s, cleanup %s',
        retention, ensured, cleaned);
END;
$$;

-- ============ 分区表重建（幂等：已是分区表则跳过） ============

DO $$
DECLARE
    is_partitioned boolean;
    legacy_count bigint := 0;
    new_count bigint := 0;
    min_month date;
    max_month date;
    m date;
    part_name text;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        WHERE c.relname = 'native_monitor_samples'
          AND c.relnamespace = 'public'::regnamespace
    ) INTO is_partitioned;

    IF NOT is_partitioned THEN
        -- 全新库：旧表不存在，直接建分区表即可。
        IF to_regclass('public.native_monitor_samples') IS NOT NULL THEN
            -- 先改名旧主键索引，避免与新表默认主键索引重名。
            ALTER INDEX IF EXISTS public.native_monitor_samples_pkey
                RENAME TO native_monitor_samples_legacy_pkey;
            ALTER TABLE public.native_monitor_samples
                RENAME TO native_monitor_samples_legacy;
        END IF;

        CREATE TABLE IF NOT EXISTS public.native_monitor_samples (
            captured_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (captured_at)
        ) PARTITION BY RANGE (captured_at);

        -- 回填旧数据：按旧数据月份范围建分区后整体 INSERT（自动路由）。
        IF to_regclass('public.native_monitor_samples_legacy') IS NOT NULL THEN
            SELECT date_trunc('month', min(captured_at))::date,
                   date_trunc('month', max(captured_at))::date
              INTO min_month, max_month
              FROM public.native_monitor_samples_legacy;
            IF min_month IS NOT NULL THEN
                m := min_month;
                WHILE m <= max_month LOOP
                    part_name := 'native_monitor_samples_p' || to_char(m, 'YYYYMM');
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.native_monitor_samples FOR VALUES FROM (%L) TO (%L)',
                        part_name, m::timestamptz, (m + make_interval(months => 1))::timestamptz);
                    m := m + make_interval(months => 1);
                END LOOP;
                INSERT INTO public.native_monitor_samples
                    SELECT * FROM public.native_monitor_samples_legacy;
                SELECT count(*) INTO legacy_count FROM public.native_monitor_samples_legacy;
                SELECT count(*) INTO new_count FROM public.native_monitor_samples;
                IF legacy_count <> new_count THEN
                    RAISE EXCEPTION '0035: 回填行数不一致 legacy=% new=%，请从备份恢复后排查',
                        legacy_count, new_count;
                END IF;
            END IF;
            DROP TABLE public.native_monitor_samples_legacy;
        END IF;
    END IF;

    -- DEFAULT 分区：兜底时钟偏移/异常时间戳写入，避免整行写入失败。
    IF to_regclass('public.native_monitor_samples_default') IS NULL THEN
        CREATE TABLE public.native_monitor_samples_default
            PARTITION OF public.native_monitor_samples DEFAULT;
    END IF;

    -- 预建未来分区（当前月起 +3 个月）。
    PERFORM public.quant_ensure_snapshot_partitions(3);
END;
$$;

-- ============ 覆盖索引 ============
-- 趋势查询 SELECT captured_at, payload ... ORDER BY captured_at DESC LIMIT 120
-- 可走 index-only 扫描；主键已覆盖排序，此索引主要省去回表取 payload。
-- 注意：PG 不允许在分区表上 CREATE INDEX CONCURRENTLY
-- （"cannot create index on partitioned table concurrently"），故此处用普通建索引；
-- 迁移时分区都是新建/小分区，构建极快，且本迁移本来就要求停机窗口（见文件头与维护说明 §13）。
CREATE INDEX IF NOT EXISTS native_monitor_samples_recent_covering
    ON public.native_monitor_samples (captured_at DESC) INCLUDE (payload);

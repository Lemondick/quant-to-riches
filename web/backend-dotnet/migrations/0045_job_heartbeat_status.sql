-- 0045_job_heartbeat_status: 对齐 job_heartbeats.last_status 枚举与原生 API 语义（batch4 流 A 真 bug 修复）。
--
-- 证据：OperationsViewsController.HeartbeatBody 校验 status ∈ {healthy, failed, blocked}
-- （缺省即 healthy），OpenAPI 已按此发布；但 0002 建表的 CHECK 仍是旧版 Python 站点的
-- {running, completed, failed, blocked}。后果：POST /api/v1/operations/heartbeats
-- 携带 status=healthy（控制器默认值）时触发 23514 → 503，心跳上报整体不可用；
-- 而旧枚举值 running/completed 反而过不了控制器校验。
-- 本迁移先搬运存量数据（completed/running→healthy），再替换 CHECK 为新枚举。幂等。
UPDATE job_heartbeats SET last_status = 'healthy' WHERE last_status IN ('completed', 'running');
ALTER TABLE job_heartbeats DROP CONSTRAINT IF EXISTS job_heartbeats_last_status_check;
ALTER TABLE job_heartbeats ADD CONSTRAINT job_heartbeats_last_status_check
    CHECK (last_status IN ('healthy', 'failed', 'blocked'));

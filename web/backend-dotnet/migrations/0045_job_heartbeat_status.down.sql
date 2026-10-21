-- 0045_job_heartbeat_status.down.sql: 回滚 0045（恢复旧枚举约束；healthy 映射回 completed）。
UPDATE job_heartbeats SET last_status = 'completed' WHERE last_status = 'healthy';
ALTER TABLE job_heartbeats DROP CONSTRAINT IF EXISTS job_heartbeats_last_status_check;
ALTER TABLE job_heartbeats ADD CONSTRAINT job_heartbeats_last_status_check
    CHECK (last_status IN ('running', 'completed', 'failed', 'blocked'));

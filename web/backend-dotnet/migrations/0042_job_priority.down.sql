-- 0042_job_priority.down.sql: 回滚 0042（移除任务优先级列与索引）。
DROP INDEX IF EXISTS idx_compute_jobs_claim;
ALTER TABLE compute_jobs DROP COLUMN IF EXISTS priority_boosted;
ALTER TABLE compute_jobs DROP COLUMN IF EXISTS priority;

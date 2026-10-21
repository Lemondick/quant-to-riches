-- 0042_job_priority: Worker 任务优先级队列（batch3 #11）。
-- compute_jobs 新增 priority（critical/high/normal）与 priority_boosted（starvation 提级标记）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。已有行默认 normal。
ALTER TABLE compute_jobs ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'normal'
    CHECK (priority IN ('critical','high','normal'));
ALTER TABLE compute_jobs ADD COLUMN IF NOT EXISTS priority_boosted BOOLEAN NOT NULL DEFAULT FALSE;
-- Claim 查询按 (status, priority, created_at) 取任务，加速排队扫描。
CREATE INDEX IF NOT EXISTS idx_compute_jobs_claim ON compute_jobs(status, priority, created_at);

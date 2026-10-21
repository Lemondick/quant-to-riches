-- 0049_dca_executions: 定投执行登记（batch5 任务4，与 smart_dca_plans 配套）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 只记录实际扣款与提醒，不自动扣款、不执行真实交易。
CREATE TABLE IF NOT EXISTS dca_executions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES smart_dca_plans(id) ON DELETE CASCADE,
    due_date DATE NOT NULL,
    executed_at TIMESTAMPTZ,
    planned_amount NUMERIC(18,2) NOT NULL CHECK (planned_amount > 0),
    actual_amount NUMERIC(18,2) CHECK (actual_amount IS NULL OR actual_amount > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'missed', 'skipped')),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS dca_executions_plan_due ON dca_executions (plan_id, due_date);
CREATE INDEX IF NOT EXISTS dca_executions_status_due ON dca_executions (status, due_date);

-- batch5-#7 慢接口优化索引（与本批主题无关的既有表索引，集中落在此迁移以遵守"仅 0048/0049 编号"约束）：
-- #2 归因报告列表 ORDER BY created_at DESC, id DESC：消除 Sort（原走 idx_attribution_user_portfolio 后仍需全量排序）。
CREATE INDEX IF NOT EXISTS attribution_reports_user_created ON attribution_reports (user_id, created_at DESC, id DESC);
-- #4 净值历史曲线 WHERE user_id AND as_of_date >= …：范围扫描替代 Seq Scan + Sort。
CREATE INDEX IF NOT EXISTS portfolio_valuations_user_asof ON portfolio_valuations (user_id, as_of_date);

COMMENT ON TABLE dca_executions IS 'batch5-4: 定投执行登记。漏投提醒：到期 QUANT_DCA_MISSED_ALERT_DAYS（默认 3 天）仍未登记 done/skipped → user_alert_center（kind=dca_missed）；登记写操作走 user_operation_audit。';

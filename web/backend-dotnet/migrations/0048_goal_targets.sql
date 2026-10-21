-- 0048_goal_targets: 目标规划（batch5 任务1）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- current_amount：创建/投影时从账本净资产取值（projection 时刷新）；测算走 worker op goal.project，
-- 只测算、不执行真实交易。
CREATE TABLE IF NOT EXISTS goal_targets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_amount NUMERIC(18,2) NOT NULL CHECK (target_amount > 0),
    target_date DATE NOT NULL,
    current_amount NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (current_amount >= 0),
    assumed_annual_return NUMERIC(8,5) NOT NULL DEFAULT 0.06
        CHECK (assumed_annual_return >= 0 AND assumed_annual_return < 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS goal_targets_user ON goal_targets (user_id);
-- batch5-#7 慢接口优化：GET /goals 按 target_date 排序，复合索引消除 Sort。
CREATE INDEX IF NOT EXISTS goal_targets_user_target_date ON goal_targets (user_id, target_date);

COMMENT ON TABLE goal_targets IS 'batch5-1: 目标规划。进度偏离按线性假设口径核对（实际完成度 vs 预期完成度），超 QUANT_GOAL_DEVIATION_THRESHOLD（默认 10%）写入 user_alert_center（kind=goal_deviation）；写操作走 user_operation_audit。';

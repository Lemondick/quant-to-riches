-- 0032_smart_dca_plans: 智能定投计划（batch2 任务5，估值驱动；与 0004 的均线版 dca_plans 并存）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 估值规则 rules_json：{"low_pct_boost": 低估加投倍数(>=0), "high_pct_cut": 高估削减比例(0-1), "halt_pct": 暂停阈值(0-1)}。
-- 只提醒、不自动扣款：到期/估值触发时写入 user_alert_center（kind=smart_dca_reminder）。
CREATE TABLE IF NOT EXISTS smart_dca_plans (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    base_amount NUMERIC(18,2) NOT NULL CHECK (base_amount > 0),
    rules_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    frequency TEXT NOT NULL DEFAULT 'monthly' CHECK (frequency IN ('weekly', 'monthly')),
    next_run_date DATE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'finished')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS smart_dca_plans_user ON smart_dca_plans (user_id);
CREATE INDEX IF NOT EXISTS smart_dca_plans_due ON smart_dca_plans (status, next_run_date) WHERE status = 'active';

COMMENT ON TABLE smart_dca_plans IS 'batch2-5: 估值驱动的智能定投计划。到期/估值触发只产生提醒（user_alert_center），不自动扣款。';

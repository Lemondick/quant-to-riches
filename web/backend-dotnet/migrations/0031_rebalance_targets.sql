-- 0031_rebalance_targets: 组合再平衡目标配置（batch2 任务1）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 语义：每用户一条目标配置。股/债/现金目标比例之和必须为 1；
-- 偏离阈值（deviation_threshold）用于偏离计算，超阈值生成调仓建议（只建议、不下单）；
-- 行业集中度上限（industry_concentration_limit）约束单一行业市值占比。
CREATE TABLE IF NOT EXISTS rebalance_targets (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    stock_ratio NUMERIC(6,4) NOT NULL DEFAULT 0.6000
        CHECK (stock_ratio >= 0 AND stock_ratio <= 1),
    bond_ratio NUMERIC(6,4) NOT NULL DEFAULT 0.3000
        CHECK (bond_ratio >= 0 AND bond_ratio <= 1),
    cash_ratio NUMERIC(6,4) NOT NULL DEFAULT 0.1000
        CHECK (cash_ratio >= 0 AND cash_ratio <= 1),
    industry_concentration_limit NUMERIC(6,4) NOT NULL DEFAULT 0.3000
        CHECK (industry_concentration_limit > 0 AND industry_concentration_limit <= 1),
    deviation_threshold NUMERIC(6,4) NOT NULL DEFAULT 0.0500
        CHECK (deviation_threshold > 0 AND deviation_threshold <= 0.5),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rebalance_targets_ratio_sum CHECK (stock_ratio + bond_ratio + cash_ratio = 1)
);

COMMENT ON TABLE rebalance_targets IS 'batch2-1: 组合再平衡目标配置。股/债/现金目标比例之和必须为1；偏离超阈值只生成"卖出X买入Y"建议，绝不下单。';

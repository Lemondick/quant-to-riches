-- 0004_smart_dca.sql
-- 智能定投 (Smart DCA) 核心表结构

CREATE TABLE dca_plans (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    symbol VARCHAR NOT NULL,             -- 定投的基金代码
    benchmark_symbol VARCHAR,            -- 参考基准，例如 '000300' (沪深300)
    base_amount NUMERIC NOT NULL,        -- 基准定投金额 (e.g. 1000)
    
    -- 均线偏离度策略参数
    ma_days INTEGER DEFAULT 250,         -- 参考均线天数 (如 250日均线)
    min_multiplier NUMERIC DEFAULT 0.5,  -- 最少定投倍数 (如 0.5x, 市场狂热时少投)
    max_multiplier NUMERIC DEFAULT 2.0,  -- 最多定投倍数 (如 2.0x, 市场低估时多投)
    
    status VARCHAR NOT NULL DEFAULT 'active', -- active, paused, finished
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dca_plans_user ON dca_plans (user_id);

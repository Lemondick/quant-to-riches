-- 0006_attribution.sql
-- 归因分析 (Attribution Analysis) 表结构
-- 基于 Brinson-Fachler 模型的绩效归因框架

CREATE TABLE attribution_reports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    portfolio_symbol VARCHAR NOT NULL,       -- 分析的投资组合/策略
    benchmark_symbol VARCHAR NOT NULL,       -- 对比基准
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    
    total_active_return NUMERIC,             -- 总超额收益 (组合收益 - 基准收益)
    allocation_effect NUMERIC,               -- 资产/行业配置效应 (Asset Allocation)
    selection_effect NUMERIC,                -- 选股/选基效应 (Stock Selection)
    interaction_effect NUMERIC,              -- 交互效应
    
    status VARCHAR NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 归因报告详情：记录各行业/分类在特定报告中的拆解明细
CREATE TABLE attribution_details (
    report_id TEXT NOT NULL REFERENCES attribution_reports(id) ON DELETE CASCADE,
    sector_name VARCHAR NOT NULL,            -- 行业/分类名称
    
    portfolio_weight NUMERIC,                -- 组合在该行业的权重
    benchmark_weight NUMERIC,                -- 基准在该行业的权重
    
    portfolio_return NUMERIC,                -- 组合在该行业的收益
    benchmark_return NUMERIC,                -- 基准在该行业的收益
    
    allocation NUMERIC,                      -- 配置效应
    selection NUMERIC,                       -- 选股效应
    interaction NUMERIC,                     -- 交互效应
    
    PRIMARY KEY (report_id, sector_name)
);

CREATE INDEX idx_attribution_user_portfolio ON attribution_reports(user_id, portfolio_symbol);

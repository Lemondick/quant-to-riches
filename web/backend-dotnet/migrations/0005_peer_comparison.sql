-- 0005_peer_comparison.sql
-- 同业对比 (Peer Comparison) 表结构

-- 1. 同类基金分组定义表（例如 "沪深300增强", "灵活配置混合型"）
CREATE TABLE peer_groups (
    id TEXT PRIMARY KEY,
    group_name VARCHAR NOT NULL,
    description TEXT,
    benchmark_symbol VARCHAR,            -- 对应的全市场基准指数 (例如 '000300' 沪深300)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 基金与分组的映射关系
CREATE TABLE fund_peer_mapping (
    symbol VARCHAR NOT NULL,             -- 基金代码
    group_id TEXT NOT NULL REFERENCES peer_groups(id) ON DELETE CASCADE,
    PRIMARY KEY (symbol, group_id)
);

-- 3. 基金同业排名历史记录 (用于预计算或快照)
CREATE TABLE peer_rankings (
    symbol VARCHAR NOT NULL,
    group_id TEXT NOT NULL REFERENCES peer_groups(id) ON DELETE CASCADE,
    data_date DATE NOT NULL,
    
    -- 当期收益率与排名
    return_1m NUMERIC,                   -- 近1个月收益率
    rank_1m INTEGER,                     -- 近1个月在同类中的绝对排名
    percentile_1m NUMERIC,               -- 近1个月百分位 (0~100，如 95% 代表击败了 95% 的同类)
    
    return_1y NUMERIC,
    rank_1y INTEGER,
    percentile_1y NUMERIC,
    
    excess_return_1y NUMERIC,            -- 相对基准的超额收益 (Alpha)
    
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (symbol, group_id, data_date)
);

CREATE INDEX idx_peer_rankings_date ON peer_rankings(data_date, group_id);

-- 0003_factors.sql
-- 多因子选基体系核心表结构

-- 因子定义表（元数据，用户隔离，支持自定义因子）
CREATE TABLE factor_definitions (
    user_id TEXT NOT NULL REFERENCES users(id),
    factor_code VARCHAR NOT NULL,
    factor_name VARCHAR NOT NULL,             -- 如 "市盈率", "20日动量"
    category VARCHAR NOT NULL,                -- 'value', 'momentum', 'quality', 'risk', 'size'
    direction INTEGER NOT NULL DEFAULT 1,     -- 1: 越大越好, -1: 越小越好
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (user_id, factor_code)
);

-- 因子暴露表（时序特征数据，用于打分与回测筛选）
CREATE TABLE factor_exposures (
    user_id TEXT NOT NULL REFERENCES users(id),
    symbol VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    data_date DATE NOT NULL,
    
    raw_value NUMERIC,                        -- 因子原始值（如 PE=15.2）
    z_score NUMERIC,                          -- 行业中性化/去极值标准化后的得分（如 1.2）
    
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (user_id, symbol, factor_code, data_date),
    FOREIGN KEY (user_id, factor_code) REFERENCES factor_definitions (user_id, factor_code) ON DELETE CASCADE
    -- 注：由于 instruments 可能为空或软删除，此处不再强约束 references instruments(user_id, symbol)
);

-- 索引：加速按日期的截面因子筛选
CREATE INDEX idx_factor_exposures_date_code ON factor_exposures (user_id, data_date, factor_code);

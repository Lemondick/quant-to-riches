-- 0030_fx_rates: 汇率表（F7 多币种本币折算）。
-- 幂等写法：可重复执行，不修改既有数据。
-- 语义：一条记录表示"1 单位 base_currency = rate 单位 quote_currency"在 observed_at 时点的快照；
-- 来源（source）与时点（observed_at）随行记录，前端折算展示时必须同时注明二者。
CREATE TABLE IF NOT EXISTS fx_rates (
    id BIGSERIAL PRIMARY KEY,
    base_currency CHAR(3) NOT NULL,
    quote_currency CHAR(3) NOT NULL,
    rate NUMERIC(18, 8) NOT NULL CHECK (rate > 0),
    source TEXT NOT NULL DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fx_rates_pair_unique UNIQUE (base_currency, quote_currency, observed_at)
);

CREATE INDEX IF NOT EXISTS fx_rates_pair_time_idx
    ON fx_rates (base_currency, quote_currency, observed_at DESC);

COMMENT ON TABLE fx_rates IS 'F7: 汇率快照。rate 表示 1 单位 base_currency 兑换 rate 单位 quote_currency；source/observed_at 随行记录供折算展示注明。';

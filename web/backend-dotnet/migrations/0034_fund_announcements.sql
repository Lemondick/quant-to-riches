-- 0034_fund_announcements: 基金公告库（batch2 任务8）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 去重：同一基金 + 标题 + 来源 URL 视为同一公告（UNIQUE 约束防重复入库）。
CREATE TABLE IF NOT EXISTS fund_announcements (
    id BIGSERIAL PRIMARY KEY,
    fund_code TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('dividend', 'manager_change', 'liquidation_risk', 'other')),
    published_at TIMESTAMPTZ,
    source_url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fund_announcements_dedup UNIQUE (fund_code, title, source_url)
);
CREATE INDEX IF NOT EXISTS fund_announcements_code_time ON fund_announcements (fund_code, published_at DESC NULLS LAST);

COMMENT ON TABLE fund_announcements IS 'batch2-8: 基金公告聚合。worker op announcements.fetch 抓取后落库，UNIQUE(fund_code,title,source_url) 去重。';

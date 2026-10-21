-- 0039_research_items: 研报/资讯聚合（batch3 流 A）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 去重：同一来源 URL 视为同一研报/资讯（UNIQUE 约束防重复入库）。
CREATE TABLE IF NOT EXISTS research_items (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'report' CHECK (kind IN ('report', 'news')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT research_items_dedup UNIQUE (url)
);
CREATE INDEX IF NOT EXISTS research_items_symbol_time ON research_items (symbol, published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS research_items_kind_time ON research_items (kind, published_at DESC NULLS LAST);

COMMENT ON TABLE research_items IS 'batch3-A: 研报/资讯聚合。worker op research.fetch 抓取后落库，UNIQUE(url) 去重。';

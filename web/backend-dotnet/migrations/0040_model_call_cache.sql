-- 0040_model_call_cache: 模型调用去重缓存（batch3 流 D）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- 缓存键 prompt_hash = sha256(model_version || canonical_json(prompt))，由流 A 后端
-- 在写入/命中时维护（命中返回 cached:true 并 hits+1，过期行由清理任务删除）。
-- 假设：hits 默认 1（首次写入即一次有效使用）；若流 A 实现改为 0 起计，以其实现为准。
CREATE TABLE IF NOT EXISTS model_call_cache (
    prompt_hash TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    result_json JSONB NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1 CHECK (hits >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS model_call_cache_expires ON model_call_cache (expires_at);

COMMENT ON TABLE model_call_cache IS 'batch3: 模型调用去重缓存。prompt_hash=sha256(model_version+canonical_json(prompt))；TTL 见 QUANT_MODEL_CACHE_TTL_MIN（默认 60 分钟）、总开关 QUANT_MODEL_CACHE_ENABLED（默认 true）。';

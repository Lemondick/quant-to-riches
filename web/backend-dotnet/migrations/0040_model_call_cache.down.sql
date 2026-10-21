-- 0040_model_call_cache.down.sql: 回滚 0040（删除模型调用去重缓存表）。
DROP TABLE IF EXISTS model_call_cache;

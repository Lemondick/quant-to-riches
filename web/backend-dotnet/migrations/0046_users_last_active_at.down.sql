-- 0046_users_last_active_at.down.sql: 回滚 0046（移除 last_active_at 列）。
ALTER TABLE users DROP COLUMN IF EXISTS last_active_at;

-- 0044_users_display_columns.down.sql: 回滚 0044（移除 display_name、timezone 列）。
ALTER TABLE users DROP COLUMN IF EXISTS timezone;
ALTER TABLE users DROP COLUMN IF EXISTS display_name;

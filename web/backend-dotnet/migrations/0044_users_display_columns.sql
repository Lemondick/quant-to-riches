-- 0044_users_display_columns: 补 users.display_name / users.timezone（batch4 流 A 真 bug 修复）。
--
-- 证据：RuntimeViewService.StatusAsync（GET /api/v1/runtime/status）执行
--   SELECT id,role,display_name,timezone,status,created_at,last_login_at,last_active_at FROM users ...
-- 但 users 表自 0001 起从未拥有 display_name、timezone 列（grep 全量迁移确认无 ADD COLUMN），
-- 导致该接口每次调用都报 42703 column "display_name" does not exist → 统一错误映射为 503，
-- 等价于运行时状态接口整体不可用。补可空列；现有行保持 NULL。
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;

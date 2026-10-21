-- 0046_users_last_active_at: 补 users.last_active_at（batch4 流 A 真 bug 修复，0044 的后续）。
--
-- 证据：RuntimeViewService.StatusAsync（GET /api/v1/runtime/status）还选取
-- last_active_at，但 0044 只补了 display_name/timezone；该列同样自 0001 起缺失
-- （grep 全量迁移确认无 ADD COLUMN），导致接口继续报 42703 → 503。
-- 本列当前无写入方，先补可空列恢复接口可用；写入方由后续需求决定。
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

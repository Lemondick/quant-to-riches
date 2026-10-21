-- 0036_audit_hash_chain.sql — 任务12：admin_audit 审计哈希链。
--
-- 新增列 prev_hash / hash（TEXT，可空：0036 之前已存在的历史行保持 NULL，
-- 链计算只覆盖 hash NOT NULL 的行；见 deploy/verify-audit-chain.sh）。
--
-- 链计算与 verify 端点（GET api/v1/admin/audit/verify）由 A 流实现
-- （AdminAuditRepository 统一写入路径）；本迁移只定义列与**标准哈希口径**，
-- 独立验证脚本 deploy/verify-audit-chain.sh 按此口径逐条验链。
--
-- == 标准哈希口径（A 流写入端必须逐字遵守，验证脚本按此验算） ==
--   preimage = prev_hash || actor_id || action || target_id || detail || created_at_utc
--   - NULL 值一律视为空字符串（COALESCE(col, '')）；
--   - 无分隔符，直接拼接；
--   - created_at_utc = to_char(created_at AT TIME ZONE 'UTC',
--                         'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
--     例：2026-10-21 12:00:00+00 -> "2026-10-21T12:00:00.000000Z"；
--   - hash = 小写 hex(SHA256(UTF-8(preimage)))。
--   创世行（id 最小）：prev_hash = ''（NULL 亦视为 ''）。
--   链性质：row[i].prev_hash 必须等于 row[i-1].hash。
--
-- 幂等：ADD COLUMN IF NOT EXISTS；重复执行安全。

ALTER TABLE public.admin_audit ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE public.admin_audit ADD COLUMN IF NOT EXISTS hash TEXT;

COMMENT ON COLUMN public.admin_audit.prev_hash IS
    '任务12：哈希链上一环 hash；创世行为空字符串。标准口径见 0036 文件头。';
COMMENT ON COLUMN public.admin_audit.hash IS
    '任务12：SHA256(prev_hash||actor_id||action||target_id||detail||created_at_utc) 小写 hex；历史行可为 NULL。';

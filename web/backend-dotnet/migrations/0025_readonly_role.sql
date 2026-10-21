-- 0025_readonly_role.sql — F6：新增只读角色 AccountRole.ReadOnly（users.role='readonly'）。
-- 幂等（E19 口径）：先删后加约束；重复执行安全。
-- 说明：single_admin 部分唯一索引不受影响（仅 role='admin' 行）。

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'user', 'readonly'));

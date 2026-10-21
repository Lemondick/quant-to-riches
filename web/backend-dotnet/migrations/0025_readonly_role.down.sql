-- 0025_readonly_role.down.sql — 回滚 0025：移除 readonly 角色值。
-- fail-closed：仍存在 readonly 用户时直接报错，拒绝回滚（避免 CHECK 约束破坏数据）。

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE role = 'readonly') THEN
        RAISE EXCEPTION '仍有 readonly 角色用户存在，不能回滚 0025_readonly_role';
    END IF;
END
$$;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'user'));

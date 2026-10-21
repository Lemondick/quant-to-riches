-- 0026_user_operation_audit.sql — F6：用户业务写操作统一审计表
-- （操作者/时间/对象/前后值摘要）+ E18：admin_audit 二级索引。
-- E18 索引放在本迁移：同属审计域，避免占用 0025（角色域）的语义。
-- 幂等（E19 口径）：IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS user_operation_audit (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    TEXT NOT NULL REFERENCES users (id),
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT,
    before_json JSONB,
    after_json  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_operation_audit_actor_created ON user_operation_audit (actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_operation_audit_target ON user_operation_audit (target_type, target_id);

-- E18：admin_audit 按 created_at DESC 分页必走全表扫描；补两个二级索引。
CREATE INDEX IF NOT EXISTS admin_audit_created_desc ON admin_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS admin_audit_actor ON admin_audit (actor_id);

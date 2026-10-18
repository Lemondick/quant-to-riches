-- 0001_auth.sql — 账号域（全局表，来自旧 SQLite data/auth/accounts.sqlite3）
-- 类型映射：SQLite TEXT→TEXT；REAL(epoch 秒)→TIMESTAMPTZ（迁移工具显式转换）；INTEGER 自增→BIGINT IDENTITY。
-- 迁移工具保持旧账号 ID、用户名、角色、停用状态与审计语义不变（计划书 §3「账号与安全」）。

CREATE TABLE users (
    id             TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at     TIMESTAMPTZ NOT NULL,
    last_login_at  TIMESTAMPTZ
);

-- 全站唯一管理员（SQLite partial unique index 的 PG 等价）
CREATE UNIQUE INDEX single_admin ON users (role) WHERE role = 'admin';

CREATE TABLE sessions (
    token_hash  TEXT PRIMARY KEY,
    session_id  TEXT,
    user_id     TEXT NOT NULL REFERENCES users (id),
    csrf        TEXT NOT NULL,
    device      TEXT NOT NULL DEFAULT '未知设备',
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX session_identity ON sessions (session_id);
CREATE INDEX user_sessions ON sessions (user_id);

CREATE TABLE site_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
INSERT INTO site_settings (key, value) VALUES ('registration_enabled', '1')
    ON CONFLICT (key) DO NOTHING;

CREATE TABLE admin_audit (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_id   TEXT,
    detail      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE usage_daily (
    user_id           TEXT NOT NULL,
    day               TEXT NOT NULL,
    category          TEXT NOT NULL,
    requests          INTEGER NOT NULL,
    errors            INTEGER NOT NULL,
    duration_ms       BIGINT NOT NULL,
    last_activity_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, day, category)
);

CREATE TABLE recovery_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL UNIQUE REFERENCES users (id),
    issued_by   TEXT NOT NULL REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE TABLE account_activity (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users (id),
    action      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX account_activity_user ON account_activity (user_id);

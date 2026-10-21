-- 0033_totp_2fa: 双重验证（batch2 任务11）。
-- users 新增 totp_secret（base32 明文密钥，未启用/关闭后为 NULL）与 totp_enabled；
-- user_totp_recovery 存恢复码哈希（Argon2id，经现有 IPasswordHasher；契约写 bcrypt，
--   本机无 bcrypt 包且任务要求不引新 NuGet，故用现有更强的 Argon2id，见 commit 说明）；
-- totp_login_challenges 存登录第二步的短期挑战（5 分钟有效，单次使用）。
-- 幂等写法（E19 口径）：ADD COLUMN IF NOT EXISTS / IF NOT EXISTS；重复执行安全。
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS user_totp_recovery (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_totp_recovery_user ON user_totp_recovery (user_id);

CREATE TABLE IF NOT EXISTS totp_login_challenges (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS totp_login_challenges_expiry ON totp_login_challenges (expires_at);

COMMENT ON TABLE user_totp_recovery IS 'batch2-11: 2FA 恢复码哈希（Argon2id）。used_at 非空表示已使用，单次有效。';
COMMENT ON TABLE totp_login_challenges IS 'batch2-11: 2FA 登录挑战。密码通过后签发，TOTP/恢复码验证通过后消费（单次有效），5 分钟过期。';

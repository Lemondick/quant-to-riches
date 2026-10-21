-- 0033_totp_2fa.down.sql: 回滚 0033（删除 2FA 相关表与 users 新增列）。
DROP TABLE IF EXISTS totp_login_challenges;
DROP TABLE IF EXISTS user_totp_recovery;
ALTER TABLE users DROP COLUMN IF EXISTS totp_secret;
ALTER TABLE users DROP COLUMN IF EXISTS totp_enabled;

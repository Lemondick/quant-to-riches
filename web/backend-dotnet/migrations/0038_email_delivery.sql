-- 0038_email_delivery: 邮件推送（batch3 流 A）。
-- 幂等写法（E19 口径）：IF NOT EXISTS；重复执行安全。
-- SMTP 配置走 site_settings（键 QUANT_SMTP_HOST/PORT/USER/PASS/FROM、QUANT_SMTP_NOTIFY_TO），不另建表；
-- 密码永不出现在 GET settings 响应与日志中（只返回 has_password）。
CREATE TABLE IF NOT EXISTS email_send_log (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    template TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
    attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_send_log_user_time ON email_send_log (user_id, created_at DESC);

COMMENT ON TABLE email_send_log IS 'batch3-A: 邮件发送记录。status=sent/failed/skipped（SMTP 未配置时记 skipped 不抛错）；attempts 含失败重试。';

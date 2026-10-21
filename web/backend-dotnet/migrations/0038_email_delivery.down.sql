-- 0038_email_delivery.down.sql: 回滚 0038（删除 email_send_log 表）。
-- 注意：site_settings 中的 QUANT_SMTP_* 配置键保留（与业务表解耦，避免误删管理员配置）。
DROP TABLE IF EXISTS email_send_log;

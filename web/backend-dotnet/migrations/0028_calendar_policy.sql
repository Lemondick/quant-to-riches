-- 0028_calendar_policy.sql（F1 日历政策的 DB 侧）
--
-- 不改表结构：只在 site_settings 增加 `calendar_policy` 设置键（JSON）。
-- 日历正文仍按现有约定存于 `calendar:<market>:<year>` 键（见
-- TradingCalendarRepository，A 流维护）；本键只存跨年份的政策开关。
--
-- provenance 约定（写入方：A 流 TradingCalendarRepository.SaveAuto）：
--   - 自动生成的日历写入 `calendar:<market>:<year>`，JSON 含 origin="auto"、
--     confirmed=false，以及 provenance 对象：
--       {"generated_by":"calendar.generate_next_year",
--        "generated_at":"<ISO8601>",
--        "source":"lunar_algorithm|nyse_rules|japan_law|hkex_rules",
--        "confidence":"high|medium|low",
--        "notes":["<需人工核对的启发式估计说明>"]}
--   - 人工按交易所官方公告录入/核验后，同一键由管理端重写为 origin="imported"、
--     confirmed=true（source 为官方公告 URL，仍须满足现有域名白名单校验）。
--   - TradingDay 采纳逻辑遵循 QUANT_CALENDAR_AUTO_ACCEPT（A 流配置，默认 true）：
--     true=采纳未确认的自动日历；false=必须人工 confirm，否则按缺失处理。
--   - 本键的 rollover_months 镜像环境变量 QUANT_CALENDAR_ROLLOVER_MONTHS
--    （默认 10,11,12；契约 §3）：进入这些月份后，状态检查开始提示准备次年日历。
--   - confirmed_years 记录已人工确认的年份列表（如 ["2026","2027"]），
--     供运维核对"哪些年份可放心跨年"；由管理端维护，本迁移只给空数组。
--
-- 幂等：INSERT ... ON CONFLICT(key) DO NOTHING。0001–0024 不动。
INSERT INTO site_settings (key, value)
VALUES ('calendar_policy', '{"policy_version":1,"auto_accept":true,"rollover_months":[10,11,12],"confirmed_years":[]}')
ON CONFLICT (key) DO NOTHING;

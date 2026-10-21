-- 0041_rate_limit_placeholder: 限流状态持久化占位迁移（batch3 流 D）。
-- 本迁移为空迁移（幂等、无操作），原因如下：
-- 1. 跨流契约的限流实现为后端内存滑动窗口（Redis-less，无需持久化计数）；
--    QUANT_RATE_LIMIT_PER_MIN（默认 100）与 QUANT_RATE_LIMIT_LOGIN_PER_MIN
--   （保持现有更严策略）均为 QuantOptions 内存配置，无落库需求。
-- 2. 若未来需要持久化限流计数（如多实例部署），另起新编号新增迁移，
--    不回头修改本占位文件（迁移 grandfather/幂等规范）。
-- 空迁移保留编号连续性，Runner 视为已应用版本。
SELECT 1;

-- 0043_cash_flow_plan_enums: 对齐 cash_flow_plans 枚举与原生 API 语义（batch4 流 A 真 bug 修复）。
--
-- 证据：
--   ① PlanningController（原生 API，OpenAPI 已发布）校验 direction ∈ {in, out}、
--      status ∈ {active, paused, archived}；
--   ② 0002_business.sql 建表时沿用了旧版 Python 站点的 CHECK 约束
--      direction ∈ {deposit, withdrawal}、status ∈ {active, paused, completed}；
--   ③ 其间无任何迁移修改过这两列的约束（grep migrations 确认）。
--   后果：任何经由 POST /api/v1/plans 的写入（direction=in/out）都会触发
--   23514 check_violation，API 返回 503，等价于资金计划功能整体不可用；
--   POST /api/v1/plans/{id}/status 设置 archived 同理。
--   本迁移先搬运存量数据（deposit→in、withdrawal→out、completed→archived），
--   再替换 CHECK 约束为新枚举。幂等写法（IF EXISTS），重复执行安全。
UPDATE cash_flow_plans SET direction = 'in' WHERE direction = 'deposit';
UPDATE cash_flow_plans SET direction = 'out' WHERE direction = 'withdrawal';
UPDATE cash_flow_plans SET status = 'archived' WHERE status = 'completed';
ALTER TABLE cash_flow_plans DROP CONSTRAINT IF EXISTS cash_flow_plans_direction_check;
ALTER TABLE cash_flow_plans ADD CONSTRAINT cash_flow_plans_direction_check
    CHECK (direction IN ('in', 'out'));
ALTER TABLE cash_flow_plans DROP CONSTRAINT IF EXISTS cash_flow_plans_status_check;
ALTER TABLE cash_flow_plans ADD CONSTRAINT cash_flow_plans_status_check
    CHECK (status IN ('active', 'paused', 'archived'));

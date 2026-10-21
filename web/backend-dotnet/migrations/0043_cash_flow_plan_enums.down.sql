-- 0043_cash_flow_plan_enums.down.sql: 回滚 0043（恢复旧枚举约束与存量数据映射）。
UPDATE cash_flow_plans SET direction = 'deposit' WHERE direction = 'in';
UPDATE cash_flow_plans SET direction = 'withdrawal' WHERE direction = 'out';
UPDATE cash_flow_plans SET status = 'completed' WHERE status = 'archived';
ALTER TABLE cash_flow_plans DROP CONSTRAINT IF EXISTS cash_flow_plans_direction_check;
ALTER TABLE cash_flow_plans ADD CONSTRAINT cash_flow_plans_direction_check
    CHECK (direction IN ('deposit', 'withdrawal'));
ALTER TABLE cash_flow_plans DROP CONSTRAINT IF EXISTS cash_flow_plans_status_check;
ALTER TABLE cash_flow_plans ADD CONSTRAINT cash_flow_plans_status_check
    CHECK (status IN ('active', 'paused', 'completed'));

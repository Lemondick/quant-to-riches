-- 0026_user_operation_audit.down.sql — 回滚 0026：删除统一审计表与 E18 索引。
-- 注意：回滚会丢失已记录的用户操作审计行；属审计数据，请先备份。

DROP INDEX IF EXISTS admin_audit_actor;
DROP INDEX IF EXISTS admin_audit_created_desc;
DROP INDEX IF EXISTS user_operation_audit_target;
DROP INDEX IF EXISTS user_operation_audit_actor_created;
DROP TABLE IF EXISTS user_operation_audit;

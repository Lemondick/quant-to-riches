-- 0037_fix_0027_triggers: 修复 0027 在真实 PG 上非法的问题（合入前必须，否则全量迁移在真实库跑不通）。
--
-- PG 限制：transition tables cannot be specified for triggers with more than one event。
-- 0027 原版创建的 position_display_revision / instrument_display_revision / document_display_revision
-- 均为 AFTER INSERT OR UPDATE OR DELETE ... REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows，
-- 在真实 PG18 上执行即报错，全量迁移 0001→0037 会中断。
--
-- 修复分两部分（缺一不可）：
--   ① 0027 已原地修复：三个多事件触发器改为 9 个单事件语句级触发器
--      （_ins/_upd/_del × positions/instruments/user_documents），函数按 TG_OP 分支。
--      原地修改安全：非法语句在任何 PG 版本上都不可能执行成功，0027 从未在真实库应用过
--      （MigrationRunner 只对已应用迁移做 sha256 漂移校验，未应用文件可改）。
--   ② 本迁移作为修复迁移保留：DROP 旧名（IF EXISTS，真实库上必然是 no-op）+
--      重建 9 个单事件触发器，幂等；覆盖"0027 已记录为已应用但触发器缺失"的理论状态。
-- 函数 quant_account_display_changed_stmt() 按 TG_OP 分支取对应的 transition table，
-- UPDATE 事件沿用 0027 的 old_rows ∪ new_rows 语义（data_revision 按用户合并递增，语义不变）。
--
-- 幂等写法（E19 口径）：DROP TRIGGER IF EXISTS + CREATE OR REPLACE FUNCTION；重复执行安全。

-- 先删掉 0027 的三个多事件触发器（真实 PG 上它们根本建不出来；IF EXISTS 保证幂等）。
DROP TRIGGER IF EXISTS position_display_revision ON positions;
DROP TRIGGER IF EXISTS instrument_display_revision ON instruments;
DROP TRIGGER IF EXISTS document_display_revision ON user_documents;

-- 0027 已原地修复为 9 个单事件触发器（见 0027 文件头）；此处一并 DROP IF EXISTS，
-- 使本迁移在"0027 已修复"和"0027 仍是旧版（理论上不可能在真实库存在）"两种状态下都幂等。
DROP TRIGGER IF EXISTS position_display_revision_ins ON positions;
DROP TRIGGER IF EXISTS position_display_revision_upd ON positions;
DROP TRIGGER IF EXISTS position_display_revision_del ON positions;
DROP TRIGGER IF EXISTS instrument_display_revision_ins ON instruments;
DROP TRIGGER IF EXISTS instrument_display_revision_upd ON instruments;
DROP TRIGGER IF EXISTS instrument_display_revision_del ON instruments;
DROP TRIGGER IF EXISTS document_display_revision_ins ON user_documents;
DROP TRIGGER IF EXISTS document_display_revision_upd ON user_documents;
DROP TRIGGER IF EXISTS document_display_revision_del ON user_documents;

-- 函数按 TG_OP 分支：INSERT 只读 new_rows，DELETE 只读 old_rows，UPDATE 读两者并集。
CREATE OR REPLACE FUNCTION quant_account_display_changed_stmt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE users AS u
        SET data_revision = data_revision + 1
        FROM (SELECT DISTINCT user_id FROM new_rows) AS v(user_id)
        WHERE u.id = v.user_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE users AS u
        SET data_revision = data_revision + 1
        FROM (SELECT DISTINCT user_id FROM old_rows) AS v(user_id)
        WHERE u.id = v.user_id;
    ELSE
        UPDATE users AS u
        SET data_revision = data_revision + 1
        FROM (
            SELECT DISTINCT user_id FROM old_rows
            UNION
            SELECT DISTINCT user_id FROM new_rows
        ) AS v(user_id)
        WHERE u.id = v.user_id;
    END IF;
    RETURN NULL;
END;
$$;

-- positions：3 个单事件触发器
CREATE TRIGGER position_display_revision_ins
    AFTER INSERT ON positions
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER position_display_revision_upd
    AFTER UPDATE ON positions
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER position_display_revision_del
    AFTER DELETE ON positions
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

-- instruments：3 个单事件触发器
CREATE TRIGGER instrument_display_revision_ins
    AFTER INSERT ON instruments
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER instrument_display_revision_upd
    AFTER UPDATE ON instruments
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER instrument_display_revision_del
    AFTER DELETE ON instruments
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

-- user_documents：3 个单事件触发器
CREATE TRIGGER document_display_revision_ins
    AFTER INSERT ON user_documents
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER document_display_revision_upd
    AFTER UPDATE ON user_documents
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();
CREATE TRIGGER document_display_revision_del
    AFTER DELETE ON user_documents
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

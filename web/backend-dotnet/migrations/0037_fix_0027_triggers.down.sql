-- 0037_fix_0027_triggers.down.sql: 回滚 0037。
--
-- 说明：0037 的 up 与修复后的 0027 最终产物完全一致（9 个单事件语句级触发器），
-- 因此回滚后数据库应保持同样可用的 9 个触发器。本脚本做幂等重建，不做语义变更。
--
-- 为什么不恢复 0027 的"三个多事件触发器"原文：
-- 0027 原文（AFTER INSERT OR UPDATE OR DELETE ... REFERENCING OLD TABLE ... NEW TABLE ...）
-- 在任何 PostgreSQL 版本上都是非法 SQL（transition tables cannot be specified for
-- triggers with more than one event），从未在真实库上执行成功过，不存在可回退的"原状态"。
-- 若把原文写进 down，rollback 会在 DROP 掉 9 个好触发器后报错，中途留下无触发器的坏库。
-- 故此处重建 9 个单事件触发器，保证回滚安全、幂等。

DROP TRIGGER IF EXISTS position_display_revision ON positions;
DROP TRIGGER IF EXISTS instrument_display_revision ON instruments;
DROP TRIGGER IF EXISTS document_display_revision ON user_documents;
DROP TRIGGER IF EXISTS position_display_revision_ins ON positions;
DROP TRIGGER IF EXISTS position_display_revision_upd ON positions;
DROP TRIGGER IF EXISTS position_display_revision_del ON positions;
DROP TRIGGER IF EXISTS instrument_display_revision_ins ON instruments;
DROP TRIGGER IF EXISTS instrument_display_revision_upd ON instruments;
DROP TRIGGER IF EXISTS instrument_display_revision_del ON instruments;
DROP TRIGGER IF EXISTS document_display_revision_ins ON user_documents;
DROP TRIGGER IF EXISTS document_display_revision_upd ON user_documents;
DROP TRIGGER IF EXISTS document_display_revision_del ON user_documents;
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

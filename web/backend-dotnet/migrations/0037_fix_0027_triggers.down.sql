-- 0037_fix_0027_triggers.down.sql: 回滚 0037（恢复 0027 的三个多事件触发器定义）。
-- 注意：0027 的多事件 + transition table 写法在真实 PG 上非法（建触发器即报错），
-- 此回滚脚本仅用于开发环境回退到 0027 语义，生产/真实库不要执行。
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
    UPDATE users AS u
    SET data_revision = data_revision + 1
    FROM (
        SELECT DISTINCT user_id FROM old_rows
        UNION
        SELECT DISTINCT user_id FROM new_rows
    ) AS v(user_id)
    WHERE u.id = v.user_id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER position_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON positions
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

CREATE TRIGGER instrument_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON instruments
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

CREATE TRIGGER document_display_revision
    AFTER INSERT OR UPDATE OR DELETE ON user_documents
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION quant_account_display_changed_stmt();

-- 0047_site_archives: 建 site_archives 表（batch4 流 A 真 bug 修复）。
--
-- 证据：RuntimeViewService.StatusAsync（GET /api/v1/runtime/status）与
-- NativeOperationsMonitor.Read 都从 site_archives 读备份归档记录，但全量迁移
-- （0001→0046）中没有任何迁移创建该表（grep 确认仅 RuntimeViewService 引用），
-- 导致该接口每次调用都报 42P01 relation "site_archives" does not exist → 503。
-- 列定义取两处查询的并集：id / finished_at / status / verified / created_at / modified_at。
-- 当前无写入方（备份证据走文件系统的 NativeBackupEvidence），空表对监视逻辑安全：
-- backup 未配置→backup_not_configured；已配置但无 verified 行→backup_missing。
CREATE TABLE IF NOT EXISTS site_archives (
    id          TEXT PRIMARY KEY,
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'unknown',
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    modified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

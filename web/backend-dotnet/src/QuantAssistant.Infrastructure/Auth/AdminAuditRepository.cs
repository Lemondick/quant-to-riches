using System.Globalization;
using System.Text.Json.Nodes;
using Npgsql;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Auth;

/// <summary>
/// batch2 任务12：admin_audit 统一写入（含哈希链）与全链验证。
/// 契约§5：admin_audit 的 prev_hash/hash 列由流 D 在 0036 添加；本类按列存在来写——
/// 每次写入先检查列是否存在（information_schema），不存在则退化为旧列写入，保证 0036 前后行为正确。
/// 写入取最新已入链行（hash IS NOT NULL）的 hash 为 prev_hash（SELECT ... FOR UPDATE 串行化并发管理员写入）；
/// 哈希口径逐字遵守流 D 0036 文件头（deploy/verify-audit-chain.sh 按此验算）。
/// </summary>
public sealed class AdminAuditRepository(AccountDatabase database, string hashAlgo = "SHA256")
{
    public string HashAlgo => hashAlgo;

    private static bool ChainEnabled(IAccountSql sql) =>
        Convert.ToInt64(sql.Scalar("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema='public' AND table_name='admin_audit' AND column_name='prev_hash'
            """)) > 0;

    /// <summary>IAccountSql 上下文写入（调用方事务内）。prev 取最新已入链行（hash IS NOT NULL）的 hash；无则创世 ''。</summary>
    public void Write(IAccountSql sql, string actorId, string action, string? targetId, string detail, DateTime createdAtUtc)
    {
        var createdAt = createdAtUtc.Kind == DateTimeKind.Utc ? createdAtUtc : createdAtUtc.ToUniversalTime();
        if (!ChainEnabled(sql))
        {
            sql.Execute("INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) VALUES($1,$2,$3,$4,$5)",
                actorId, action, targetId, detail, createdAt);
            return;
        }
        var prev = sql.Query("SELECT hash FROM admin_audit WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1 FOR UPDATE")
            .FirstOrDefault()?["hash"]?.GetValue<string>() ?? AdminAuditChain.GenesisPrevHash;
        var hash = AdminAuditChain.ComputeHash(hashAlgo, prev, actorId, action, targetId, detail, createdAt);
        sql.Execute("INSERT INTO admin_audit(actor_id,action,target_id,detail,prev_hash,hash,created_at) VALUES($1,$2,$3,$4,$5,$6,$7)",
            actorId, action, targetId, detail, prev, hash, createdAt);
    }

    /// <summary>原始 Npgsql 事务内写入（AccountRepository.InsertAudit 用，复用其 conn/tx）。</summary>
    public static void WriteRaw(NpgsqlConnection conn, NpgsqlTransaction tx, string hashAlgo,
        string actorId, string action, string? targetId, string detail, DateTime createdAtUtc)
    {
        var createdAt = createdAtUtc.Kind == DateTimeKind.Utc ? createdAtUtc : createdAtUtc.ToUniversalTime();
        using var check = conn.CreateCommand();
        check.Transaction = tx;
        check.CommandText = "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='admin_audit' AND column_name='prev_hash'";
        var chainEnabled = Convert.ToInt64(check.ExecuteScalar()) > 0;
        if (!chainEnabled)
        {
            using var legacy = conn.CreateCommand();
            legacy.Transaction = tx;
            legacy.CommandText = "INSERT INTO admin_audit (actor_id, action, target_id, detail, created_at) VALUES ($1, $2, $3, $4, $5)";
            legacy.Parameters.AddWithValue(actorId);
            legacy.Parameters.AddWithValue(action);
            legacy.Parameters.AddWithValue((object?)targetId ?? DBNull.Value);
            legacy.Parameters.AddWithValue(detail);
            legacy.Parameters.AddWithValue(createdAt);
            legacy.ExecuteNonQuery();
            return;
        }
        string prev;
        using (var latest = conn.CreateCommand())
        {
            latest.Transaction = tx;
            latest.CommandText = "SELECT hash FROM admin_audit WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1 FOR UPDATE";
            prev = latest.ExecuteScalar() as string ?? AdminAuditChain.GenesisPrevHash;
        }
        var hash = AdminAuditChain.ComputeHash(hashAlgo, prev, actorId, action, targetId, detail, createdAt);
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "INSERT INTO admin_audit (actor_id, action, target_id, detail, prev_hash, hash, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)";
        cmd.Parameters.AddWithValue(actorId);
        cmd.Parameters.AddWithValue(action);
        cmd.Parameters.AddWithValue((object?)targetId ?? DBNull.Value);
        cmd.Parameters.AddWithValue(detail);
        cmd.Parameters.AddWithValue(prev);
        cmd.Parameters.AddWithValue(hash);
        cmd.Parameters.AddWithValue(createdAt);
        cmd.ExecuteNonQuery();
    }

    /// <summary>
    /// 全链验证（口径与 deploy/verify-audit-chain.sh 一致）：
    /// 仅验算 hash IS NOT NULL 的行（0036 前的遗留行跳过，计入 legacy_rows 警告）；
    /// 逐条重算 hash 并核对 prev_hash 链接（首个入链行的 prev_hash 须为 ''）。
    /// 返回 {enabled, status: ok|broken, count, legacy_rows, broken_at?, expected?, actual?}。
    /// </summary>
    public JsonObject Verify() => database.Read(sql =>
    {
        if (!ChainEnabled(sql))
            return new JsonObject { ["enabled"] = false, ["error_code"] = "audit_chain_not_enabled",
                ["message"] = "admin_audit 缺少 prev_hash/hash 列，哈希链未启用（需应用迁移 0035）。" };
        var legacy = Convert.ToInt64(sql.Scalar("SELECT count(*) FROM admin_audit WHERE hash IS NULL"));
        var rows = sql.Query("SELECT id,actor_id,action,target_id,detail,prev_hash,hash,created_at FROM admin_audit WHERE hash IS NOT NULL ORDER BY id");
        var prev = AdminAuditChain.GenesisPrevHash;
        var count = 0;
        foreach (var row in rows.OfType<JsonObject>())
        {
            count++;
            var id = row["id"]!.GetValue<long>();
            var storedPrev = row["prev_hash"]?.GetValue<string>() ?? "";
            var storedHash = row["hash"]?.GetValue<string>() ?? "";
            // Db 层把 timestamptz 映射为带时区偏移的字符串；按瞬间转 UTC 后套标准口径格式化。
            var createdAt = DateTime.Parse(row["created_at"]!.GetValue<string>(), CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.RoundtripKind);
            var expected = AdminAuditChain.ComputeHash(hashAlgo, storedPrev, row["actor_id"]!.GetValue<string>(),
                row["action"]!.GetValue<string>(), row["target_id"]?.GetValue<string>(), row["detail"]!.GetValue<string>(), createdAt);
            if (storedPrev != prev || !string.Equals(storedHash, expected, StringComparison.Ordinal))
                return new JsonObject { ["enabled"] = true, ["status"] = "broken", ["count"] = count, ["legacy_rows"] = legacy,
                    ["broken_at"] = id, ["expected"] = expected, ["actual"] = storedHash, ["prev_expected"] = prev, ["prev_actual"] = storedPrev };
            prev = storedHash;
        }
        return new JsonObject { ["enabled"] = true, ["status"] = "ok", ["count"] = count, ["legacy_rows"] = legacy,
            ["algo"] = hashAlgo, ["verified_at"] = DateTimeOffset.UtcNow.ToString("O") };
    });
}

using System.Security.Cryptography;
using Npgsql;

namespace QuantAssistant.Infrastructure.Db;

/// <summary>
/// 版本化 SQL 迁移执行器：按文件名字典序应用 migrations/*.sql，记录于 schema_migrations 表。
/// 支持空环境建库与幂等重复执行（已应用的迁移跳过）。
///
/// E2：
/// - 漂移检测：已应用文件的 SHA256 记入 schema_migrations.sha256；文件被改过则拒绝执行并报错。
///   （0001–0024 老迁移原样 grandfather：首次运行时对尚无校验和的行回填当前文件哈希并打印提示，
///   之后纳入漂移检测；迁移文件内容本身绝不改写。）
/// - 回滚：--rollback-to &lt;version&gt; 按版本倒序执行同名 .down.sql；缺 down 文件直接拒绝并提示。
/// - dry-run：只列出待应用迁移，不执行。
/// - --no-transaction（预留接口，E19/C 使用文件侧机制）：CLI 标记或文件首行
///   "-- quant:no-transaction" 标记时，迁移在显式事务外执行（供 CREATE INDEX CONCURRENTLY 类语句）。
/// </summary>
public sealed class MigrationRunner(string connectionString)
{
    public sealed record MigrationOptions(bool DryRun = false, string? RollbackTo = null, bool NoTransaction = false);

    private const string NoTransactionMarker = "-- quant:no-transaction";

    public IReadOnlyList<string> Apply(string migrationsDirectory, MigrationOptions? options = null)
    {
        options ??= new();
        var files = Directory.GetFiles(migrationsDirectory, "*.sql")
            .Where(file => !file.EndsWith(".down.sql", StringComparison.OrdinalIgnoreCase))
            .OrderBy(Path.GetFileName, StringComparer.Ordinal).ToList();

        using var connection = new NpgsqlConnection(new NpgsqlConnectionStringBuilder(connectionString) { Pooling = false }.ConnectionString);
        connection.Open();
        using (var gate = connection.CreateCommand())
        {
            gate.CommandText = "SELECT pg_advisory_lock(hashtextextended('schema:migrations', 0))";
            gate.ExecuteNonQuery();
        }
        try
        {
            EnsureMigrationsTable(connection);
            var applied = ReadApplied(connection);
            VerifyNoDrift(connection, files, applied);

            if (options.RollbackTo is not null)
                return RollbackTo(connection, files, applied, options.RollbackTo, options.DryRun);

            var pending = files.Where(file => !applied.ContainsKey(Path.GetFileName(file))).ToList();
            if (options.DryRun)
                return pending.Select(Path.GetFileName).ToList()!;

            var done = new List<string>();
            foreach (var file in pending)
            {
                ApplyFile(connection, file, options.NoTransaction || HasNoTransactionMarker(file));
                done.Add(Path.GetFileName(file));
            }
            return done;
        }
        finally
        {
            using var gate = connection.CreateCommand();
            gate.CommandText = "SELECT pg_advisory_unlock(hashtextextended('schema:migrations', 0))";
            gate.ExecuteNonQuery();
        }
    }

    private static void EnsureMigrationsTable(NpgsqlConnection connection)
    {
        using var cmd = connection.CreateCommand();
        cmd.CommandText =
            "CREATE TABLE IF NOT EXISTS schema_migrations (" +
            "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())";
        cmd.ExecuteNonQuery();
        cmd.CommandText = "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS sha256 TEXT";
        cmd.ExecuteNonQuery();
    }

    private static Dictionary<string, string?> ReadApplied(NpgsqlConnection connection)
    {
        var applied = new Dictionary<string, string?>(StringComparer.Ordinal);
        using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT version, sha256 FROM schema_migrations";
        using var reader = cmd.ExecuteReader();
        while (reader.Read()) applied[reader.GetString(0)] = reader.IsDBNull(1) ? null : reader.GetString(1);
        return applied;
    }

    private static string Sha256(string file) =>
        Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(file))).ToLowerInvariant();

    private static void VerifyNoDrift(NpgsqlConnection connection, List<string> files, Dictionary<string, string?> applied)
    {
        foreach (var file in files)
        {
            var version = Path.GetFileName(file);
            if (!applied.TryGetValue(version, out var recorded)) continue;
            var current = Sha256(file);
            if (recorded is null)
            {
                // Grandfather：老迁移首次纳入校验和（内容不改写），打印提示以便审计。
                using var cmd = connection.CreateCommand();
                cmd.CommandText = "UPDATE schema_migrations SET sha256=$2 WHERE version=$1";
                cmd.Parameters.AddWithValue(version);
                cmd.Parameters.AddWithValue(current);
                cmd.ExecuteNonQuery();
                Console.WriteLine($"Checksum backfilled (grandfathered, file untouched): {version}");
                continue;
            }
            if (!string.Equals(recorded, current, StringComparison.Ordinal))
                throw new InvalidOperationException(
                    $"迁移文件漂移：{version} 的内容与应用时不一致（schema_migrations.sha256 校验失败）。" +
                    "请核对文件是否被篡改；如需变更请新增迁移，不要直接改已应用文件。");
        }
    }

    private static bool HasNoTransactionMarker(string file)
    {
        foreach (var line in File.ReadLines(file))
        {
            var trimmed = line.Trim();
            if (trimmed.Length == 0) continue;
            return trimmed.StartsWith(NoTransactionMarker, StringComparison.Ordinal);
        }
        return false;
    }

    private static void ApplyFile(NpgsqlConnection connection, string file, bool noTransaction)
    {
        var version = Path.GetFileName(file);
        var sql = File.ReadAllText(file);
        if (noTransaction)
        {
            using var cmd = connection.CreateCommand();
            cmd.CommandText = sql;
            cmd.ExecuteNonQuery();
        }
        else
        {
            using var transaction = connection.BeginTransaction();
            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = sql;
                cmd.ExecuteNonQuery();
            }
            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = "INSERT INTO schema_migrations (version, sha256) VALUES ($1, $2)";
                cmd.Parameters.AddWithValue(version);
                cmd.Parameters.AddWithValue(Sha256(file));
                cmd.ExecuteNonQuery();
            }
            transaction.Commit();
        }
        if (noTransaction)
        {
            using var cmd = connection.CreateCommand();
            cmd.CommandText = "INSERT INTO schema_migrations (version, sha256) VALUES ($1, $2)";
            cmd.Parameters.AddWithValue(version);
            cmd.Parameters.AddWithValue(Sha256(file));
            cmd.ExecuteNonQuery();
        }
    }

    private static IReadOnlyList<string> RollbackTo(NpgsqlConnection connection, List<string> files, Dictionary<string, string?> applied, string target, bool dryRun)
    {
        var byVersion = files.ToDictionary(file => Path.GetFileName(file)!, StringComparer.Ordinal);
        var victims = applied.Keys.Where(version => string.Compare(version, target, StringComparison.Ordinal) > 0)
            .OrderByDescending(version => version, StringComparer.Ordinal).ToList();
        if (dryRun) return victims;
        var rolledBack = new List<string>();
        foreach (var version in victims)
        {
            if (!byVersion.TryGetValue(version, out var upFile))
                throw new InvalidOperationException($"回滚被拒绝：{version} 在迁移目录中找不到对应的 .sql 文件，无法确认回滚脚本。");
            var downFile = Path.ChangeExtension(upFile, null) + ".down.sql";
            if (!File.Exists(downFile))
                throw new InvalidOperationException(
                    $"回滚被拒绝：{version} 缺少同名 .down.sql 回滚脚本（应为 {Path.GetFileName(downFile)}）。" +
                    "请先补充回滚脚本，或改用整库备份恢复。");
            using var transaction = connection.BeginTransaction();
            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = File.ReadAllText(downFile);
                cmd.ExecuteNonQuery();
            }
            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = "DELETE FROM schema_migrations WHERE version=$1";
                cmd.Parameters.AddWithValue(version);
                cmd.ExecuteNonQuery();
            }
            transaction.Commit();
            rolledBack.Add(version);
        }
        return rolledBack;
    }
}

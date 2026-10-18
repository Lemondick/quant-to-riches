using Npgsql;

namespace QuantAssistant.Infrastructure.Db;

/// <summary>
/// 版本化 SQL 迁移执行器：按文件名字典序应用 migrations/*.sql，记录于 schema_migrations 表。
/// 支持空环境建库与幂等重复执行（已应用的迁移跳过）。
/// </summary>
public sealed class MigrationRunner(string connectionString)
{
    public IReadOnlyList<string> Apply(string migrationsDirectory)
    {
        var files = Directory.GetFiles(migrationsDirectory, "*.sql").OrderBy(Path.GetFileName, StringComparer.Ordinal).ToList();
        if (files.Count == 0)
        {
            return [];
        }

        var applied = new List<string>();
        using var connection = new NpgsqlConnection(new NpgsqlConnectionStringBuilder(connectionString) { Pooling = false }.ConnectionString);
        connection.Open();
        using (var gate = connection.CreateCommand())
        {
            gate.CommandText = "SELECT pg_advisory_lock(hashtextextended('schema:migrations', 0))";
            gate.ExecuteNonQuery();
        }

        using (var cmd = connection.CreateCommand())
        {
            cmd.CommandText =
                "CREATE TABLE IF NOT EXISTS schema_migrations (" +
                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())";
            cmd.ExecuteNonQuery();
        }

        var done = new HashSet<string>();
        using (var cmd = connection.CreateCommand())
        {
            cmd.CommandText = "SELECT version FROM schema_migrations";
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                done.Add(reader.GetString(0));
            }
        }

        foreach (var file in files)
        {
            var version = Path.GetFileName(file);
            if (done.Contains(version))
            {
                continue;
            }

            using var transaction = connection.BeginTransaction();
            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = File.ReadAllText(file);
                cmd.ExecuteNonQuery();
            }

            using (var cmd = connection.CreateCommand())
            {
                cmd.Transaction = transaction;
                cmd.CommandText = "INSERT INTO schema_migrations (version) VALUES ($1)";
                cmd.Parameters.AddWithValue(version);
                cmd.ExecuteNonQuery();
            }

            transaction.Commit();
            applied.Add(version);
        }

        using (var gate = connection.CreateCommand())
        {
            gate.CommandText = "SELECT pg_advisory_unlock(hashtextextended('schema:migrations', 0))";
            gate.ExecuteNonQuery();
        }
        return applied;
    }
}

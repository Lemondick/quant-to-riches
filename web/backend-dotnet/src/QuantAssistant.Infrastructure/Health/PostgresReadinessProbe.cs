using Npgsql;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Health;

public sealed class PostgresReadinessProbe(string connectionString) : IReadinessProbe
{
    public HealthStatus Check()
    {
        try
        {
            var settings = new NpgsqlConnectionStringBuilder(connectionString) { Timeout = 2, CommandTimeout = 2 };
            using var connection = new NpgsqlConnection(settings.ConnectionString);
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = """
                SELECT NOT EXISTS(SELECT 1 FROM unnest(ARRAY['users','transactions','attribution_reports',
                    'user_documents','compute_jobs','worker_heartbeats','model_call_costs','review_sessions',
                    'ledger_replay_previews','migration_batches','site_restore_runs']) table_name WHERE to_regclass(table_name) IS NULL)
                    AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                        AND table_name='compute_jobs' AND column_name='worker_id')
                """;
            return command.ExecuteScalar() is true ? new("ok", "connected") : new("not_ready", "schema_missing");
        }
        catch (NpgsqlException) { return new("not_ready", "unavailable"); }
        catch (TimeoutException) { return new("not_ready", "unavailable"); }
    }
}

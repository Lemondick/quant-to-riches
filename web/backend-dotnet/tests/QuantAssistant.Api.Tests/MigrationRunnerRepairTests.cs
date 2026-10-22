using Npgsql;
using QuantAssistant.Infrastructure.Db;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class MigrationRunnerRepairTests
{
    private sealed class Fixture : IDisposable
    {
        private readonly string schema = "repair_migration_" + Guid.NewGuid().ToString("N");
        private readonly string rootConnection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION")!;
        public string Directory { get; } = Path.Combine(Path.GetTempPath(), "quant-migration-repair-" + Guid.NewGuid().ToString("N"));
        public string Connection { get; }
        public MigrationRunner Runner => new(Connection);

        public Fixture()
        {
            System.IO.Directory.CreateDirectory(Directory);
            using var connection = new NpgsqlConnection(rootConnection);
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = $"CREATE SCHEMA \"{schema}\"";
            command.ExecuteNonQuery();
            Connection = new NpgsqlConnectionStringBuilder(rootConnection) { SearchPath = schema, Pooling = false }.ConnectionString;
        }

        public void File(string name, string sql) => System.IO.File.WriteAllText(Path.Combine(Directory, name), sql);

        public object? Sql(string sql)
        {
            using var connection = new NpgsqlConnection(Connection);
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = sql;
            return command.ExecuteScalar();
        }

        public void Dispose()
        {
            using var connection = new NpgsqlConnection(rootConnection);
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = $"DROP SCHEMA \"{schema}\" CASCADE";
            command.ExecuteNonQuery();
            System.IO.Directory.Delete(Directory, recursive: true);
        }
    }

    [PgFact]
    public void DryRun_DoesNotCreateMetadataOrBackfillLegacyChecksums()
    {
        using var fixture = new Fixture();
        fixture.File("0001_test.sql", "CREATE TABLE evidence(value integer)");
        Assert.Equal(["0001_test.sql"], fixture.Runner.Apply(fixture.Directory, new(DryRun: true)));
        Assert.Equal(0L, fixture.Sql("SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()"));
        fixture.Sql("CREATE TABLE schema_migrations(version text PRIMARY KEY, applied_at timestamptz DEFAULT now()); INSERT INTO schema_migrations(version) VALUES('0001_test.sql')");
        Assert.Empty(fixture.Runner.Apply(fixture.Directory, new(DryRun: true)));
        Assert.Equal(0L, fixture.Sql("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='schema_migrations' AND column_name='sha256'"));
    }

    [PgFact]
    public void MissingRollbackScript_IsRejectedBeforeAnyVersionIsRemoved()
    {
        using var fixture = new Fixture();
        fixture.File("0001_test.sql", "CREATE TABLE evidence(value integer); INSERT INTO evidence VALUES(1)");
        fixture.File("0002_test.sql", "ALTER TABLE evidence ADD COLUMN note text");
        fixture.File("0003_test.sql", "INSERT INTO evidence VALUES(3,'retained')");
        fixture.File("0003_test.down.sql", "DELETE FROM evidence WHERE value=3");
        Assert.Equal(3, fixture.Runner.Apply(fixture.Directory).Count);
        Assert.Throws<InvalidOperationException>(() => fixture.Runner.Apply(fixture.Directory, new(RollbackTo: "0001_test.sql")));
        Assert.Equal(2L, fixture.Sql("SELECT count(*) FROM evidence"));
        Assert.Equal(3L, fixture.Sql("SELECT count(*) FROM schema_migrations"));
    }

    [PgFact]
    public void ApplyRollbackAndDriftChecks_PreserveCommittedEvidence()
    {
        using var fixture = new Fixture();
        fixture.File("0001_test.sql", "CREATE TABLE evidence(value integer); INSERT INTO evidence VALUES(1)");
        fixture.File("0002_test.sql", "INSERT INTO evidence VALUES(2)");
        fixture.File("0002_test.down.sql", "DELETE FROM evidence WHERE value=2");
        Assert.Equal(2, fixture.Runner.Apply(fixture.Directory).Count);
        Assert.Empty(fixture.Runner.Apply(fixture.Directory));
        Assert.Equal(["0002_test.sql"], fixture.Runner.Apply(fixture.Directory, new(RollbackTo: "0001_test.sql")));
        Assert.Equal(1L, fixture.Sql("SELECT count(*) FROM evidence"));
        Assert.Equal(["0002_test.sql"], fixture.Runner.Apply(fixture.Directory));
        fixture.File("0001_test.sql", "DROP TABLE evidence");
        Assert.Throws<InvalidOperationException>(() => fixture.Runner.Apply(fixture.Directory));
        Assert.Equal(2L, fixture.Sql("SELECT count(*) FROM evidence"));
    }
}

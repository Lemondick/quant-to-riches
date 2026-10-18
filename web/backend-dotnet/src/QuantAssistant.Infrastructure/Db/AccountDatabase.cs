using System.Globalization;
using System.Text.Json.Nodes;
using Npgsql;

namespace QuantAssistant.Infrastructure.Db;

/// <summary>Small SQL session for account-owned application data. SQL comes only from server code.</summary>
public sealed class AccountDatabase(string connectionString)
{
    public async Task<JsonArray> QueryAsync(string sql, object?[] values, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        return await new AccountSql(connection, null, cancellation).QueryAsync(sql, values);
    }

    public async Task<T> ReadAsync<T>(Func<AccountSql, Task<T>> read, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(System.Data.IsolationLevel.RepeatableRead, cancellation);
        var result = await read(new AccountSql(connection, transaction, cancellation));
        await transaction.CommitAsync(cancellation);
        return result;
    }

    public async Task<T> WriteAsync<T>(string userId, Func<AccountSql, Task<T>> write, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        var session = new AccountSql(connection, transaction, cancellation);
        await session.ExecuteAsync("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ledger:" + userId);
        if (userId != "compute:reaper" && await session.ScalarAsync("SELECT 1 FROM users WHERE id=$1 AND status='active' FOR SHARE", userId) is null)
            throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
        var result = await write(session);
        await transaction.CommitAsync(cancellation);
        return result;
    }

    public T Read<T>(Func<AccountSql, T> read) => ReadAsync(sql => Task.FromResult(read(sql))).GetAwaiter().GetResult();

    public T Write<T>(string userId, Func<AccountSql, T> write) => WriteAsync(userId, sql => Task.FromResult(write(sql))).GetAwaiter().GetResult();
}

public sealed class AccountSql(NpgsqlConnection connection, NpgsqlTransaction? transaction, CancellationToken cancellation = default)
{
    public async Task<int> ExecuteAsync(string sql, params object?[] values)
    {
        await using var command = Command(sql, values);
        return await command.ExecuteNonQueryAsync(cancellation);
    }

    public async Task<object?> ScalarAsync(string sql, params object?[] values)
    {
        await using var command = Command(sql, values);
        var value = await command.ExecuteScalarAsync(cancellation);
        return value is DBNull ? null : value;
    }

    public async Task<JsonArray> QueryAsync(string sql, params object?[] values)
    {
        await using var command = Command(sql, values);
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        var result = new JsonArray();
        while (await reader.ReadAsync(cancellation)) result.Add(ReadRow(reader));
        return result;
    }

    private NpgsqlCommand Command(string sql, object?[] values)
    {
        var command = new NpgsqlCommand(sql, connection, transaction);
        foreach (var value in values) command.Parameters.AddWithValue(value ?? DBNull.Value);
        return command;
    }

    public int Execute(string sql, params object?[] values) => ExecuteAsync(sql, values).GetAwaiter().GetResult();

    public object? Scalar(string sql, params object?[] values) => ScalarAsync(sql, values).GetAwaiter().GetResult();

    public JsonArray Query(string sql, params object?[] values) => QueryAsync(sql, values).GetAwaiter().GetResult();

    private static JsonObject ReadRow(NpgsqlDataReader reader)
    {
            var row = new JsonObject();
            for (var index = 0; index < reader.FieldCount; index++)
            {
                var name = reader.GetName(index);
                var value = reader.IsDBNull(index) ? null : reader.GetValue(index);
                row[name] = value switch
                {
                    null => null,
                    decimal amount => JsonValue.Create(amount.ToString(CultureInfo.InvariantCulture)),
                    DateTime date when reader.GetDataTypeName(index) == "date" => JsonValue.Create(date.ToString("yyyy-MM-dd")),
                    DateTime time => JsonValue.Create(new DateTimeOffset(DateTime.SpecifyKind(time, DateTimeKind.Utc)).ToOffset(TimeSpan.FromHours(8)).ToString("O")),
                    DateOnly date => JsonValue.Create(date.ToString("yyyy-MM-dd")),
                    string json when reader.GetDataTypeName(index) is "json" or "jsonb" => JsonNode.Parse(json),
                    _ => System.Text.Json.JsonSerializer.SerializeToNode(value),
                };
            }
            return row;
    }
}

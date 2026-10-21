using System.Globalization;
using System.Text.Json.Nodes;
using Npgsql;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Db;

/// <summary>Small SQL session for account-owned application data. SQL comes only from server code.</summary>
public sealed class AccountDatabase(string connectionString) : IAccountDatabase
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

    async Task<T> IAccountDatabase.ReadAsync<T>(Func<IAccountSql, Task<T>> read, CancellationToken cancellation) =>
        await ReadAsync(sql => read(sql), cancellation);

    /// <summary>
    /// 账本写：取 pg_advisory_xact_lock("ledger:"+userId) 将同一用户的账本变更串行化。
    /// 仅用于 transactions/positions/dividend_events/corporate_action_events/ledger_corrections
    /// 等账本事实表的变更；其余写操作请用 <see cref="WriteNonLedgerAsync"/>（行级锁即可）。
    /// </summary>
    public async Task<T> WriteAsync<T>(string userId, Func<AccountSql, Task<T>> write, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        var session = new AccountSql(connection, transaction, cancellation);
        await session.ExecuteAsync("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ledger:" + userId);
        await RequireActiveUserAsync(session, userId);
        var result = await write(session);
        await transaction.CommitAsync(cancellation);
        return result;
    }

    async Task<T> IAccountDatabase.WriteAsync<T>(string userId, Func<IAccountSql, Task<T>> write, CancellationToken cancellation) =>
        await WriteAsync(userId, sql => write(sql), cancellation);

    /// <summary>
    /// 普通写：不行级串行化，仅事务 + 行级锁 + 活跃用户校验。
    /// 账本事实表之外的所有写操作（设置、计划、告警、任务、快照等）都应走这里，
    /// 缩小临界区，避免同一用户的所有写被全局串行化（E15）。
    /// </summary>
    public async Task<T> WriteNonLedgerAsync<T>(string userId, Func<AccountSql, Task<T>> write, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        var session = new AccountSql(connection, transaction, cancellation);
        await RequireActiveUserAsync(session, userId);
        var result = await write(session);
        await transaction.CommitAsync(cancellation);
        return result;
    }

    private static async Task RequireActiveUserAsync(AccountSql session, string userId)
    {
        // 注意：此处必须用 FOR UPDATE 而非 FOR SHARE。同一用户的并发写事务稍后会经
        // quant_account_display_changed_stmt 触发器执行 UPDATE users（行独占锁）；
        // 若此处仅持共享锁，两个并发事务会在“共享→独占”升级时形成死锁（40P01）。
        // FOR UPDATE 让并发写按用户串行等待，消除升级死锁。
        if (userId != "compute:reaper" && await session.ScalarAsync("SELECT 1 FROM users WHERE id=$1 AND status='active' FOR UPDATE", userId) is null)
            throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
    }

    public T Read<T>(Func<AccountSql, T> read) => ReadAsync(sql => Task.FromResult(read(sql))).GetAwaiter().GetResult();

    public T Write<T>(string userId, Func<AccountSql, T> write) => WriteAsync(userId, sql => Task.FromResult(write(sql))).GetAwaiter().GetResult();

    /// <summary>同步普通写（供 CLI/低频流程）。</summary>
    public T WriteNonLedger<T>(string userId, Func<AccountSql, T> write) => WriteNonLedgerAsync(userId, sql => Task.FromResult(write(sql))).GetAwaiter().GetResult();

    async Task<T> IAccountDatabase.WriteNonLedgerAsync<T>(string userId, Func<IAccountSql, Task<T>> write, CancellationToken cancellation) =>
        await WriteNonLedgerAsync(userId, sql => write(sql), cancellation);
}

public sealed class AccountSql(NpgsqlConnection connection, NpgsqlTransaction? transaction, CancellationToken cancellation = default) : IAccountSql
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

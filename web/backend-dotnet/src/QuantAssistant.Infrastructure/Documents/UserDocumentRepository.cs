using System.Text.Json.Nodes;
using Npgsql;

namespace QuantAssistant.Infrastructure.Documents;

/// <summary>Atomic account-owned non-financial settings; financial facts stay in ledger tables.</summary>
public sealed class UserDocumentRepository(string connectionString)
{
    public JsonObject Read(string userId, string key, Func<JsonObject> defaults) => ReadAsync(userId, key, defaults).GetAwaiter().GetResult();

    public async Task<JsonObject> ReadAsync(string userId, string key, Func<JsonObject> defaults, CancellationToken cancellation = default)
    {
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(key);
        return await cmd.ExecuteScalarAsync(cancellation) is string value ? JsonNode.Parse(value)!.AsObject() : defaults();
    }

    public JsonObject Update(string userId, string key, Func<JsonObject> defaults, Func<JsonObject, JsonObject> update) => UpdateAsync(userId, key, defaults, update).GetAwaiter().GetResult();

    public async Task<JsonObject> UpdateAsync(string userId, string key, Func<JsonObject> defaults, Func<JsonObject, JsonObject> update, CancellationToken cancellation = default)
    {
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync(cancellation);
        await using var tx = await conn.BeginTransactionAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended($1,0))";
        cmd.Parameters.AddWithValue("document:" + userId + ":" + key);
        await cmd.ExecuteNonQueryAsync(cancellation);
        cmd.Parameters.Clear();
        cmd.CommandText = "SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(key);
        var current = await cmd.ExecuteScalarAsync(cancellation) is string value ? JsonNode.Parse(value)!.AsObject() : defaults();
        var replacement = update(current.DeepClone().AsObject());
        if (!JsonNode.DeepEquals(current, replacement))
        {
            replacement["revision"] = (current["revision"]?.GetValue<long>() ?? 0) + 1;
            replacement["updated_at"] = DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("O");
            cmd.Parameters.Clear();
            cmd.CommandText = """
                INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,$3,$4::jsonb)
                ON CONFLICT(user_id,document_key) DO UPDATE SET revision=EXCLUDED.revision,payload=EXCLUDED.payload,updated_at=now()
                """;
            cmd.Parameters.AddWithValue(userId);
            cmd.Parameters.AddWithValue(key);
            cmd.Parameters.AddWithValue(replacement["revision"]!.GetValue<long>());
            cmd.Parameters.AddWithValue(replacement.ToJsonString());
            await cmd.ExecuteNonQueryAsync(cancellation);
        }
        await tx.CommitAsync(cancellation);
        return replacement;
    }
}

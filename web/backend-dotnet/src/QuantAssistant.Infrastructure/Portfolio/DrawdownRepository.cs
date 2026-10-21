using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>E5：回撤验证仓储（原 DrawdownController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class DrawdownRepository(IAccountDatabase database)
{
    private const string Columns = "run_id,threshold,max_drawdown,observed_sessions,breach_count,passed,reasons_json AS reasons,research_only";

    public async Task<JsonNode?> ReadAsync(string userId, string? runId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            (await sql.QueryAsync($"SELECT {Columns} FROM drawdown_validations WHERE user_id=$1 AND ($2::text IS NULL OR run_id=$2) ORDER BY validated_at DESC,run_id DESC LIMIT 1", userId, runId)).FirstOrDefault(), cancellation);

    public async Task<JsonNode> ValidateAsync(string userId, string runId, JsonObject report, double threshold,
        double maxDrawdown, int sessions, int breaches, bool passed, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var existing = (await sql.QueryAsync($"SELECT {Columns} FROM drawdown_validations WHERE user_id=$1 AND run_id=$2", userId, runId)).FirstOrDefault();
            if (existing is not null)
            {
                if (!JsonNode.DeepEquals(existing, report)) throw new ArgumentException("相同运行ID已有不同回撤结果");
                return existing;
            }
            await sql.ExecuteAsync("INSERT INTO drawdown_validations(user_id,run_id,threshold,max_drawdown,observed_sessions,breach_count,passed,research_only,reasons_json,validated_at) VALUES($1,$2,$3,$4,$5,$6,$7,true,$8::json,now())",
                userId, runId, threshold, maxDrawdown, sessions, breaches, passed, report["reasons"]!.ToJsonString());
            return (JsonNode)report;
        }, cancellation);
}

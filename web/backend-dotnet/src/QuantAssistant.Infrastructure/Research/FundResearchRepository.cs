using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：基金研究仓储（原 FundResearchController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class FundResearchRepository(IAccountDatabase database)
{
    public async Task<JsonObject> InputAsync(string userId, JsonObject? extra = null, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
        {
            extra ??= new();
            extra["pointer"] = await sql.ScalarAsync("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key='study-pointer'", userId) is string raw ? JsonNode.Parse(raw) : null;
            return extra;
        }, cancellation);

    public async Task SaveSnapshotsAsync(string userId, JsonArray snapshots, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            foreach (var row in snapshots.OfType<JsonObject>())
            {
                var raw = row["snapshot_json"]!.GetValue<string>();
                var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
                var symbol = row["symbol"]!.GetValue<string>();
                if (hash != row["fingerprint"]!.GetValue<string>() || FundFeeRepository.Symbol(symbol) != symbol)
                    throw new ArgumentException("研究观察校验失败");
                await sql.ExecuteAsync("INSERT INTO fund_research_snapshots(user_id,fingerprint,symbol,observed_at,snapshot_json) VALUES($1,$2,$3,$4,$5::json) ON CONFLICT(user_id,fingerprint) DO NOTHING",
                    userId, hash, symbol, DateTimeOffset.Parse(row["observed_at"]!.GetValue<string>()).UtcDateTime, raw);
            }
            return 0;
        }, cancellation);

    public async Task<JsonObject> UpdateStatusAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
        {
            var row = (await sql.QueryAsync("SELECT job_id,status,result_json,error_code,created_at,completed_at FROM compute_jobs WHERE user_id=$1 AND kind='fund.study_update' ORDER BY created_at DESC LIMIT 1", userId)).FirstOrDefault()?.AsObject();
            if (row is null) return new JsonObject { ["status"] = "idle", ["message"] = "尚未发起研究更新。", ["model_calls"] = 0 };
            if (row["status"]!.GetValue<string>() == "completed") { var result = row["result_json"]!.AsObject(); result.Remove("pointer"); return result.DeepClone().AsObject(); }
            row["status"] = row["status"]!.GetValue<string>() is "queued" or "running" ? "running" : "failed";
            row.Remove("result_json"); row["model_calls"] = 0;
            row["message"] = row["status"]!.GetValue<string>() == "running" ? "更新已开始；完成校验前继续展示旧版。" : "更新未完成，旧版保留；请显式重试。";
            return row.AsObject();
        }, cancellation);

    public async Task<JsonObject> EnqueueUpdateAsync(string userId, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var active = await sql.ScalarAsync("SELECT job_id FROM compute_jobs WHERE user_id=$1 AND kind='fund.study_update' AND status IN('queued','running') LIMIT 1", userId) as string;
            var id = active;
            if (id is null)
            {
                var input = new JsonObject
                {
                    ["snapshot"] = AnalysisSnapshotRepository.Create(sql, userId), ["study_id"] = Guid.NewGuid().ToString("N"),
                    ["pointer"] = await sql.ScalarAsync("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key='study-pointer'", userId) is string raw ? JsonNode.Parse(raw) : null,
                };
                id = ComputeJobRepository.EnqueueOwned(sql, userId, "fund.study_update", input.ToJsonString(), DateTime.UtcNow.AddHours(1));
            }
            return new JsonObject { ["job_id"] = id, ["status"] = "running", ["model_calls"] = 0, ["message"] = "更新已开始；完成校验前继续展示旧版。" };
        }, cancellation);
}

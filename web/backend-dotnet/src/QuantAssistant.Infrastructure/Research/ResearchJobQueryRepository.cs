using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：研究任务查询仓储（原 ResearchController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class ResearchJobQueryRepository(IAccountDatabase database)
{
    public async Task<JsonArray> ListJobsAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.QueryAsync("SELECT job_id,symbol,status,error_code,attempt_no,created_at FROM agent_research_jobs WHERE user_id=$1 ORDER BY created_at DESC,job_id LIMIT $2", [userId, limit], cancellation);

    public async Task<JsonArray> ListPreviewsAsync(string userId, CancellationToken cancellation = default) =>
        await database.QueryAsync("SELECT snapshot_id,created_at,expires_at,payload_json->'candidates' AS candidates,payload_json->'blockers' AS blockers FROM agent_preview_snapshots WHERE user_id=$1 AND expires_at>now() ORDER BY created_at DESC LIMIT 30", [userId], cancellation);
}

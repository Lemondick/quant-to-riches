using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：决策运行存在性仓储（原 ExplanationController 内嵌 SQL 迁移至此）。</summary>
public sealed class DecisionRunRepository(IAccountDatabase database)
{
    public async Task<bool> ExistsAsync(string userId, string runId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            await sql.ScalarAsync("SELECT 1 FROM strategy_runs WHERE user_id=$1 AND run_id=$2", userId, runId) is not null, cancellation);
}

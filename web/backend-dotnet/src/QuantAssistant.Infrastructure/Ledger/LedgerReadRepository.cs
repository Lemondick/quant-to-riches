using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Ledger;

/// <summary>E5：账本读仓储（原 LedgerReadController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class LedgerReadRepository(IAccountDatabase database)
{
    public async Task<JsonArray> CorporateActionsAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.QueryAsync("SELECT id,action_type,symbol,target_symbol,asset_type,quantity,target_quantity,price,target_price,gross_amount,fees,cash_delta,ratio,effective_at,external_id,source,from_account,to_account,note FROM corporate_action_events WHERE user_id=$1 ORDER BY effective_at DESC,id DESC LIMIT $2", [userId, limit], cancellation);

    public async Task<JsonArray> CorrectionsAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.QueryAsync("SELECT id,target_type,target_id,reason,effective_at,external_id,source,note FROM ledger_corrections WHERE user_id=$1 ORDER BY effective_at DESC,id DESC LIMIT $2", [userId, limit], cancellation);

    public async Task<JsonArray> PortfolioHistoryAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.QueryAsync("""
            SELECT run_id,as_of_date,generated_at,total_value,cash,unit_nav,high_water_mark,net_external_flow,is_baseline FROM
            (SELECT run_id,as_of_date,generated_at,total_value,cash,unit_nav,high_water_mark,net_external_flow,is_baseline FROM portfolio_valuations WHERE user_id=$1 ORDER BY as_of_date DESC,generated_at DESC,run_id DESC LIMIT $2) recent
            ORDER BY as_of_date,generated_at,run_id
            """, [userId, limit], cancellation);
}
